# TenderAI Unified Tender Processor
# Ver. 1.1.0

"""
Unified Tender Ingestion & Processing Engine:
1. Document Triage: Filters out noise/boilerplate/unrelated drawings from a tender package directory.
2. Raw Context Assembly: Preserves full raw text of retained relevant files.
3. Structured Field Extraction: Extracts exact metadata (title, dates, contacts, values, URLs).
4. Summarisation: Generates a concise headline and comprehensive description for bid evaluation.
5. DB Record Construction: Builds a complete record formatted for the BigQuery database schema.

System prompts, taxonomies, and data transformation instructions are loaded externally
from `tender_processor.cfg` so they can be edited without altering this code.

Requirements:
    pip install pydantic tenacity google-genai

Environment:
    export GEMINI_API_KEY="your_api_key_here"
    export TENDER_PROCESSOR_CONFIG="/path/to/custom_config.cfg"  # optional
"""

import os
import json
import time
import configparser
from datetime import datetime, timezone
from typing import Optional

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

# Initialize Gemini Client
client = genai.Client(
    vertexai=True,
    project="tenderai-dev",
    location="australia-southeast1",
)

def is_retryable_error(exc: BaseException) -> bool:
    """Retry on Vertex/Gemini server errors (5xx) and rate limits (429 RESOURCE_EXHAUSTED)."""
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.APIError) and exc.code == 429:
        return True
    return False


# ==============================================================================
# Pydantic Output Schemas
# (Field descriptions are dynamically populated from tender_processor.cfg)
# ==============================================================================

class DocumentRelevance(BaseModel):
    relevant: bool = Field(...)
    reason: Optional[str] = Field(default=None)


class TenderSummary(BaseModel):
    headline: str = Field(...)
    description: str = Field(...)


class TenderFields(BaseModel):
    source_id: Optional[str] = Field(default=None)
    source_reference_id: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)
    issuing_agency: Optional[str] = Field(default=None)
    category: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
    publish_date: Optional[str] = Field(default=None)
    closing_date: Optional[str] = Field(default=None)
    value_amount: Optional[float] = Field(default=None)
    value_currency: Optional[str] = Field(default=None)
    value_notes: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    tags: list[str] = Field(default_factory=list)
    contact_name: Optional[str] = Field(default=None)
    contact_email: Optional[str] = Field(default=None)
    contact_phone: Optional[str] = Field(default=None)
    lodgment_address: Optional[str] = Field(default=None)


# ==============================================================================
# Configuration Loader & Prompt Manager
# ==============================================================================

_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tender_processor.cfg")
_CONFIG_PATH: str = _DEFAULT_CONFIG_PATH
_LAST_CONFIG_MTIME: Optional[float] = None

# Global configuration variables populated by load_config()
TRIAGE_MODEL: str = "gemini-2.0-flash-lite"
EXTRACTION_MODEL: str = "gemini-2.5-flash"
MODEL: str = EXTRACTION_MODEL
TRIAGE_TEMPERATURE: float = 0.1
SUMMARY_TEMPERATURE: float = 0.2
EXTRACTION_TEMPERATURE: float = 0.1
TRIAGE_CHAR_LIMIT: int = 6000

TAG_TAXONOMY: list[str] = []
DOC_TRIAGE_SYSTEM_INSTRUCTION: str = ""
SUMMARY_SYSTEM_INSTRUCTION: str = ""
FIELD_EXTRACTION_SYSTEM_INSTRUCTION: str = ""
PROMPT_TEMPLATES: dict[str, str] = {}


def _parse_lenient_cfg(filepath: str) -> configparser.ConfigParser:
    """
    Parses a .cfg file leniently so that multiline prompts and blank lines within
    prompt blocks do not cause ConfigParser errors.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    processed_lines = []
    in_value = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Blank line inside a multiline value or between sections
            if in_value:
                processed_lines.append("    \n")
            else:
                processed_lines.append("\n")
        elif stripped.startswith(("[", ";", "#")):
            in_value = False
            processed_lines.append(line)
        elif "=" in line and not line.startswith((" ", "\t")):
            in_value = True
            processed_lines.append(line)
        else:
            # Continuation line; ensure indentation
            if in_value and not line.startswith((" ", "\t")):
                processed_lines.append("    " + line)
            else:
                processed_lines.append(line)

    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string("".join(processed_lines))
    return parser


def load_config(config_path: Optional[str] = None):
    """
    Loads models, taxonomies, system instructions, and schema descriptions from an external .cfg file.
    Updates module-level globals and re-binds Pydantic schema field descriptions.
    """
    global _CONFIG_PATH, _LAST_CONFIG_MTIME
    global TRIAGE_MODEL, EXTRACTION_MODEL, MODEL
    global TRIAGE_TEMPERATURE, SUMMARY_TEMPERATURE, EXTRACTION_TEMPERATURE, TRIAGE_CHAR_LIMIT
    global TAG_TAXONOMY
    global DOC_TRIAGE_SYSTEM_INSTRUCTION, SUMMARY_SYSTEM_INSTRUCTION, FIELD_EXTRACTION_SYSTEM_INSTRUCTION
    global PROMPT_TEMPLATES

    resolved_path = config_path or os.getenv("TENDER_PROCESSOR_CONFIG") or _DEFAULT_CONFIG_PATH

    if not os.path.isfile(resolved_path):
        # Check current working directory as fallback
        alt_path = os.path.join(os.getcwd(), "tender_processor.cfg")
        if os.path.isfile(alt_path):
            resolved_path = alt_path
        else:
            raise FileNotFoundError(
                f"Tender processor configuration file not found at '{resolved_path}'. "
                f"Please ensure tender_processor.cfg exists or set TENDER_PROCESSOR_CONFIG."
            )

    cfg = _parse_lenient_cfg(resolved_path)

    # 1. Models and hyperparameters
    if cfg.has_section("models"):
        TRIAGE_MODEL = cfg.get("models", "triage_model", fallback=TRIAGE_MODEL)
        EXTRACTION_MODEL = cfg.get("models", "extraction_model", fallback=EXTRACTION_MODEL)
        MODEL = EXTRACTION_MODEL
        TRIAGE_TEMPERATURE = cfg.getfloat("models", "triage_temperature", fallback=TRIAGE_TEMPERATURE)
        SUMMARY_TEMPERATURE = cfg.getfloat("models", "summary_temperature", fallback=SUMMARY_TEMPERATURE)
        EXTRACTION_TEMPERATURE = cfg.getfloat("models", "extraction_temperature", fallback=EXTRACTION_TEMPERATURE)
        TRIAGE_CHAR_LIMIT = cfg.getint("models", "triage_char_limit", fallback=TRIAGE_CHAR_LIMIT)

    # 2. Taxonomies / Categorisation
    if cfg.has_section("taxonomies"):
        raw_tags = cfg.get("taxonomies", "tags", fallback="")
        TAG_TAXONOMY.clear()
        TAG_TAXONOMY.extend([t.strip() for t in raw_tags.split(",") if t.strip()])

    # 3. System Instructions
    if cfg.has_section("system_prompts"):
        DOC_TRIAGE_SYSTEM_INSTRUCTION = cfg.get("system_prompts", "doc_triage", fallback="").strip()
        SUMMARY_SYSTEM_INSTRUCTION = cfg.get("system_prompts", "summary", fallback="").strip()
        FIELD_EXTRACTION_SYSTEM_INSTRUCTION = cfg.get("system_prompts", "field_extraction", fallback="").strip()

    # 4. Field Descriptions (Data transformation & categorization instructions for Pydantic schemas)
    if cfg.has_section("field_descriptions"):
        descriptions = dict(cfg.items("field_descriptions"))

        # Update DocumentRelevance schema
        if "relevance_relevant" in descriptions:
            DocumentRelevance.model_fields["relevant"].description = descriptions["relevance_relevant"]
        if "relevance_reason" in descriptions:
            DocumentRelevance.model_fields["reason"].description = descriptions["relevance_reason"]
        DocumentRelevance.model_rebuild(force=True)

        # Update TenderSummary schema
        if "summary_headline" in descriptions:
            TenderSummary.model_fields["headline"].description = descriptions["summary_headline"]
        if "summary_description" in descriptions:
            TenderSummary.model_fields["description"].description = descriptions["summary_description"]
        TenderSummary.model_rebuild(force=True)

        # Update TenderFields schema
        tags_str = ", ".join(TAG_TAXONOMY)
        for field_name in TenderFields.model_fields.keys():
            cfg_key = f"field_{field_name}"
            if cfg_key in descriptions:
                desc = descriptions[cfg_key]
                if "{tags}" in desc:
                    desc = desc.format(tags=tags_str)
                TenderFields.model_fields[field_name].description = desc
        TenderFields.model_rebuild(force=True)

    # 5. Prompt Formatting Templates
    if cfg.has_section("prompt_templates"):
        PROMPT_TEMPLATES.clear()
        PROMPT_TEMPLATES.update(dict(cfg.items("prompt_templates")))

    # Update cache tracking
    _CONFIG_PATH = resolved_path
    try:
        _LAST_CONFIG_MTIME = os.path.getmtime(resolved_path)
    except OSError:
        _LAST_CONFIG_MTIME = None


def _check_auto_reload():
    """Checks if the configuration file on disk has been updated, and reloads if necessary."""
    global _CONFIG_PATH, _LAST_CONFIG_MTIME
    if _CONFIG_PATH and os.path.isfile(_CONFIG_PATH):
        try:
            mtime = os.path.getmtime(_CONFIG_PATH)
            if _LAST_CONFIG_MTIME is not None and mtime > _LAST_CONFIG_MTIME:
                load_config(_CONFIG_PATH)
        except OSError:
            pass


# Initial load upon module import
load_config()


# ==============================================================================
# Document Helpers
# ==============================================================================

def iter_tender_documents(directory: str):
    """Yield paths to every .txt document in a tender's directory."""
    for name in sorted(os.listdir(directory)):
        if name.lower().endswith(".txt"):
            yield os.path.join(directory, name)


def list_tender_documents(directory: str) -> list[dict]:
    """
    Reads every .txt in directory into {file_name, extracted_text} - this
    is the raw per-document list for the DB record.
    """
    documents = []
    for path in iter_tender_documents(directory):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        documents.append({
            "file_name": os.path.basename(path),
            "extracted_text": text,
        })
    return documents


@retry(
    retry=retry_if_exception(is_retryable_error),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def is_document_relevant(path: str) -> bool:
    """Check if a document is relevant to the tender extraction and summary process."""
    _check_auto_reload()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    # Empty or near-empty documents are not relevant
    if not text.strip():
        return False

    text_snippet = text[:TRIAGE_CHAR_LIMIT]
    file_name = os.path.basename(path)
    template = PROMPT_TEMPLATES.get("triage_user_prompt", "Filename: {file_name}\n\n{text_snippet}")
    contents = template.format(file_name=file_name, text_snippet=text_snippet)

    response = client.models.generate_content(
        model=TRIAGE_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=DOC_TRIAGE_SYSTEM_INSTRUCTION,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            response_mime_type="application/json",
            response_schema=DocumentRelevance,
            temperature=TRIAGE_TEMPERATURE,
        ),
    )
    
    decision: DocumentRelevance = response.parsed
    return decision.relevant


def gather_relevant_documents(documents_dir: str) -> list[dict]:
    """
    1. Triages every .txt in documents_dir to drop useless files (blank templates,
       pure CAD tables, boilerplate clauses).
    2. Keeps the raw text of all relevant documents without lossy compression.
    """
    _check_auto_reload()
    doc_paths = list(iter_tender_documents(documents_dir))
    if not doc_paths:
        return []

    # If there is only one document (e.g. the scraped tender landing page), it is inherently relevant
    if len(doc_paths) == 1:
        with open(doc_paths[0], "r", encoding="utf-8", errors="replace") as f:
            return [{"file_name": os.path.basename(doc_paths[0]), "raw_text": f.read()}]

    relevant_docs = []
    for path in doc_paths:
        try:
            if is_document_relevant(path):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    relevant_docs.append({
                        "file_name": os.path.basename(path),
                        "raw_text": f.read(),
                    })
        except Exception as e:
            # One problematic document shouldn't kill the whole tender processing
            print(f"  [skipped {os.path.basename(path)} during triage: {e}]")
            continue
        finally:
            # Polite pacing between document triage requests to smooth out burst rates
            time.sleep(0.5)

    return relevant_docs


def build_tender_context(relevant_documents: list[dict]) -> str:
    """
    Combines the raw text of all relevant documents into a structured context block.
    """
    if not relevant_documents:
        return "(no relevant source documents found)"

    doc_template = PROMPT_TEMPLATES.get("tender_context_doc", "=== DOCUMENT: {file_name} ===\n{raw_text}")
    parts = []
    for doc in relevant_documents:
        parts.append(doc_template.format(file_name=doc["file_name"], raw_text=doc["raw_text"]))
    return "\n\n".join(parts)


def build_prompt(raw_context: str | None) -> str:
    """
    raw_context: combined raw text from the kept relevant documents.
    """
    empty_template = PROMPT_TEMPLATES.get(
        "tender_prompt_empty",
        "## Tender Source Documents\n\n(no relevant documents found)"
    )
    header_template = PROMPT_TEMPLATES.get(
        "tender_prompt_header",
        "## Tender Source Documents\n\n{raw_context}"
    )

    if not raw_context or not raw_context.strip():
        return empty_template

    return header_template.format(raw_context=raw_context)


# ==============================================================================
# AI Extraction & Summarisation Calls
# ==============================================================================

@retry(
    retry=retry_if_exception(is_retryable_error),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def summarise_tender(raw_context: str | None) -> TenderSummary:
    """Generates headline and description from raw context."""
    _check_auto_reload()
    prompt = build_prompt(raw_context)

    response = client.models.generate_content(
        model=EXTRACTION_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SUMMARY_SYSTEM_INSTRUCTION,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            response_mime_type="application/json",
            response_schema=TenderSummary,
            temperature=SUMMARY_TEMPERATURE,
        ),
    )
    return response.parsed


@retry(
    retry=retry_if_exception(is_retryable_error),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def extract_tender_fields(raw_context: str | None) -> TenderFields:
    """Extracts structured fields directly from raw context."""
    _check_auto_reload()
    prompt = build_prompt(raw_context)

    response = client.models.generate_content(
        model=EXTRACTION_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=FIELD_EXTRACTION_SYSTEM_INSTRUCTION,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            response_mime_type="application/json",
            response_schema=TenderFields,
            temperature=EXTRACTION_TEMPERATURE,
        ),
    )

    fields: TenderFields = response.parsed
    # Filter tags to only allowed taxonomy
    fields.tags = [t for t in fields.tags if t in TAG_TAXONOMY]
    return fields


# ==============================================================================
# Unified Pipeline & Database Record Builder
# ==============================================================================

def process_tender(documents_dir: str) -> dict:
    """
    Full tender processing pipeline:
    1. Triages and drops irrelevant files, keeping raw text of relevant ones.
    2. Runs summarisation & field extraction directly from the raw context.
    3. Formats fields to match the BigQuery database schema.
    """
    print("start of process_tender function")
    _check_auto_reload()

    relevant_docs = gather_relevant_documents(documents_dir)
    raw_context = build_tender_context(relevant_docs)
    documents = list_tender_documents(documents_dir)

    # 1. AI Summarisation & Extraction
    summary = summarise_tender(raw_context)
    fields = extract_tender_fields(raw_context)

    # 2. Clean dates for BigQuery DATE format (YYYY-MM-DD)
    publish_date_bq = fields.publish_date.split("T")[0] if fields.publish_date else None
    closing_date_bq = fields.closing_date.split("T")[0] if fields.closing_date else None

    # 3. Pack tags or extra metadata into raw_extra
    raw_extra = json.dumps({"tags": fields.tags}) if fields.tags else None

    return {
        "tender_id": None,
        "source_reference_id": fields.source_reference_id,
        "source_id": fields.source_id,
        "title": fields.title,
        "issuing_agency": fields.issuing_agency,
        "category": fields.category,
        "status": fields.status,
        "publish_date": publish_date_bq,
        "closing_date": closing_date_bq,
        "value_amount": fields.value_amount,
        "value_currency": fields.value_currency,
        "value_notes": fields.value_notes,
        "location": fields.location,
        "description": summary.description,
        "contact_name": fields.contact_name,
        "contact_email": fields.contact_email,
        "contact_phone": fields.contact_phone,
        "lodgment_address": fields.lodgment_address,
        "documents": documents,
        "content_hash": None,
        "tags": None,
        "first_seen_at": None,
        "last_scanned_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": None,
        "raw_extra": raw_extra,
    }
