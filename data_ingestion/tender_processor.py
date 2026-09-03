# TenderAI Unified Tender Processor
# Ver. 1.0.0

"""
Unified Tender Ingestion & Processing Engine:
1. Document Triage: Filters out noise/boilerplate/unrelated drawings from a tender package directory.
2. Raw Context Assembly: Preserves full raw text of retained relevant files.
3. Structured Field Extraction: Extracts exact metadata (title, dates, contacts, values, URLs).
4. Summarisation: Generates a concise headline and comprehensive description for bid evaluation.
5. DB Record Construction: Builds a complete record formatted for the BigQuery database schema.

Requirements:
    pip install pydantic tenacity google-genai

Environment:
    export GEMINI_API_KEY="your_api_key_here"
"""

import os
import json
from datetime import datetime, timezone
from typing import Optional

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# Initialize Gemini Client
client = genai.Client(
    vertexai = True,
    project="tenderai-dev",
    location="australia-southeast1")

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

# ===
# Tag Taxonomy
# ===
TAG_TAXONOMY = [
    "construction",
    "IT & software",
    "professional services",
    "supply of goods",
    "maintenance",
    "consulting",
]


# ==============================================================================
# Pydantic Output Schemas
# ==============================================================================

class DocumentRelevance(BaseModel):
    relevant: bool = Field(
        description=(
            "True if this document contains information relevant to understanding "
            "the tender, extracting key metadata (title, agency, dates, contacts, "
            "reference IDs, URLs), scope of work, contract value/term, deliverables, "
            "mandatory criteria, evaluation methodology, addenda, or submission instructions. "
            "False if it is pure boilerplate (standard unamended contract templates), "
            "blank response forms, or low-level engineering drawings / raw CAD / spec sheets "
            "with no administrative or bid-evaluation value."
        )
    )
    reason: Optional[str] = Field(
        default=None,
        description="Brief 1-sentence reason for keeping or dropping the document."
    )


class TenderSummary(BaseModel):
    headline: str = Field(
        description=(
            "One sentence, max ~20 words. What is being procured and by whom. "
            "No agency boilerplate, no dates, no 'this is a notice that...' framing. "
            "e.g. 'AAD seeks supplier to design, build and deliver a new Antarctic "
            "fuel storage system.'"
        )
    )
    description: str = Field(
        description=(
            "2-4 short paragraphs for a client deciding whether this tender is "
            "worth pursuing. Cover: what's actually being procured and the scope "
            "of work; who is responsible for what (what's in scope vs explicitly "
            "excluded); contract value/term if stated; key dates (publish, close, "
            "delivery timeframes); and anything unusual (e.g. pre-release notice "
            "vs live tender, restrictions on contacting the agency). Do not pad "
            "with generic procurement language. If a section (e.g. value) is not "
            "stated in the source material, omit it rather than guessing."
        )
    )


class TenderFields(BaseModel):
    source_id: Optional[str] = Field(
        description=(
            "The tender's own reference number/ID as stated in the source "
            "(e.g. an ATM ID or tender number like '1222692568' or '26-0084'). None if "
            "not stated."
        )
    )
    source_reference_id: Optional[str] = Field(
        description=(
            "A URL for viewing the tender online, if one appears in the "
            "source text (e.g. a 'Detail URL' line). None if no URL is "
            "present anywhere in the material."
        )
    )
    title: Optional[str] = Field(
        description="The tender's title/name as stated in the source. None if genuinely unclear."
    )
    issuing_agency: Optional[str] = Field(
        description="The agency/department/organisation running the tender. None if genuinely unclear."
    )
    category: Optional[str] = Field(
        description=(
            "The type of procurement notice, exactly as labelled in the "
            "source (e.g. 'Request for Tender', 'Request for Quotation', 'Notice', 'Expression of "
            "Interest'). Record it as stated, don't normalise it into some "
            "other wording. None if not labelled anywhere."
        )
    )
    status: Optional[str] = Field(
        description=(
            "The tender's current status, exactly as stated in the source "
            "(e.g. 'Open', 'Closed', 'Awarded'), lowercased. None if not "
            "stated."
        )
    )
    publish_date: Optional[str] = Field(
        description=(
            "ISO 8601 date (YYYY-MM-DD) the tender/notice was published. "
            "None if not stated anywhere."
        )
    )
    closing_date: Optional[str] = Field(
        description=(
            "ISO 8601 date, or full datetime with UTC offset if a specific "
            "close time and timezone are given (e.g. '2026-08-18T14:00:00+10:00'). "
            "None if not stated, or if this is a pre-release notice with no "
            "close date set yet."
        )
    )
    value_amount: Optional[float] = Field(
        description=(
            "A single numeric contract value, no currency symbol or commas "
            "(e.g. 250000.0). Only set this if the source gives ONE clean "
            "figure. Never estimate, calculate, or average a range into a "
            "single number - if it's a range, or there's no exact figure, "
            "leave this None and put the detail in value_notes instead."
        )
    )
    value_currency: Optional[str] = Field(
        description=(
            "3-letter currency code (e.g. 'AUD'). Use AUD if a $ figure is "
            "given with no other currency stated, since this is an "
            "Australian tender portal. None if no value is stated at all."
        )
    )
    value_notes: Optional[str] = Field(
        description=(
            "Value info that doesn't reduce to a single clean number - a "
            "range (e.g. '$500,000 - $1,000,000 AUD'), an estimate, or a "
            "qualifier like 'excl. GST'. None if value_amount alone covers "
            "it, or if no value is stated anywhere."
        )
    )
    location: Optional[str] = Field(
        description="Where the work/delivery takes place (state(s), city, or specific site). None if not stated."
    )
    tags: list[str] = Field(
        default_factory=list,
        description=(
            f"0-5 tags describing this tender, chosen ONLY from this list: "
            f"{', '.join(TAG_TAXONOMY)}. Never invent a tag outside this "
            "list. Empty list if nothing fits well."
        ),
    )
    contact_name: Optional[str] = Field(
        description="Name of the named contact person or contact entity for enquiries. None if not stated."
    )
    contact_email: Optional[str] = Field(
        description="Contact email for enquiries. None if not stated."
    )
    contact_phone: Optional[str] = Field(
        description="Contact phone number for enquiries. None if not stated."
    )
    lodgment_address: Optional[str] = Field(
        description=(
            "Where/how to submit a response (portal, email, or physical "
            "address). If the source explicitly says this isn't available "
            "yet (e.g. 'refer to ATM documents once released'), record that "
            "statement rather than leaving it blank - that's still real "
            "info, different from it just not being mentioned at all. Only "
            "use None if the source says nothing about lodgment whatsoever."
        )
    )


# ==============================================================================
# System Instructions
# ==============================================================================

DOC_TRIAGE_SYSTEM_INSTRUCTION = """You are triaging documents from an Australian government tender package.
Your job is to decide whether this document contains useful content for understanding the tender, extracting key metadata (agency, dates, contact info, reference IDs, value, location, etc.), or evaluating scope and requirements.

Mark relevant=True for:
- Tender landing pages and overview notices (always relevant)
- Statement of Requirements / Scope of Works / Approach to Market
- Addenda, clarifications, and Q&A documents
- Evaluation criteria, conditions of participation, specifications with substantive scope

Mark relevant=False for:
- Blank response form templates
- Standard legal contract boilerplate with no custom terms (e.g. generic Commonwealth Contracting Suite terms)
- Pure technical drawing tables, part-number lists, or low-level CAD schedules with no high-level scope context
"""

SUMMARY_SYSTEM_INSTRUCTION = """You are a procurement analyst summarising Australian
government tender notices for a business development team
deciding which tenders to pursue.

Rules:
- Base your summary ONLY on the raw source documents provided. Never invent
  contract values, dates, or scope details that aren't present.
- Government tender notices are full of repeated legal boilerplate.
  Do not summarise the boilerplate itself — extract the substance underneath it.
- Focus on what's actually being procured, scope of work, key dates, eligibility,
  mandatory requirements, contract value/term, and anything unusual.
- If two documents repeat the same information, say it once.
- Write for someone skimming a list of tenders. Be concrete: names, numbers,
  quantities, locations.
"""

FIELD_EXTRACTION_SYSTEM_INSTRUCTION = """You are extracting structured data
from raw scraped text documents of an Australian government tender (landing page
and any relevant attachments) for insertion into a database.

Rules:
- Every field must come directly from the source material. Never infer,
  estimate, or calculate a value that isn't explicitly stated - this
  matters most for the value fields and the dates. If it's not there,
  leave it None.
- title/category/status/source_id/source_reference_id normally live on
  the tender's landing page / notice header, but pull them from wherever
  they appear.
- source_id: The tender's reference number / ATM ID (e.g. '26-0084').
- source_reference_id: Detail URL / link for viewing the tender online.
- Contact info: Extract contact_name, contact_email, and contact_phone under enquiry/contact sections.
- Lodgment: lodgment_address is where/how to actually submit a response (e.g. portal name/URL, email, physical address).
- Distinguish "not stated anywhere" (-> None) from "explicitly stated as
  not yet available" (-> record that statement). Both are different from
  guessing, and both are useful, just not the same thing.
- Dates: normalise to ISO 8601. Include time and timezone offset for
  closing_date only if the source actually gives them.
- Value: only fill value_amount when there's one clean figure. Ranges,
  estimates, and qualifiers go in value_notes instead, and value_amount
  stays None in that case.
- Tags: only ever choose from the provided list. If nothing fits well,
  return an empty list rather than forcing a loose match.
"""


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


def is_document_relevant(path: str) -> bool:
    """Check if a document is relevant to the tender extraction and summary process."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    # Empty or near-empty documents are not relevant
    if not text.strip():
        return False

    response = client.models.generate_content(
        model=MODEL,
        contents=f"Filename: {os.path.basename(path)}\n\n{text[:15000]}",
        config=types.GenerateContentConfig(
            system_instruction=DOC_TRIAGE_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=DocumentRelevance,
            temperature=0.1,
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

    return relevant_docs


def build_tender_context(relevant_documents: list[dict]) -> str:
    """
    Combines the raw text of all relevant documents into a structured context block.
    """
    if not relevant_documents:
        return "(no relevant source documents found)"

    parts = []
    for doc in relevant_documents:
        parts.append(f"=== DOCUMENT: {doc['file_name']} ===\n{doc['raw_text']}")
    return "\n\n".join(parts)


def build_prompt(raw_context: str | None) -> str:
    """
    raw_context: combined raw text from the kept relevant documents.
    """
    if not raw_context or not raw_context.strip():
        return "## Tender Source Documents\n\n(no relevant documents found)"

    return f"## Tender Source Documents\n\n{raw_context}"


# ==============================================================================
# AI Extraction & Summarisation Calls
# ==============================================================================

@retry(
    retry=retry_if_exception_type(genai_errors.ServerError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def summarise_tender(raw_context: str | None) -> TenderSummary:
    """Generates headline and description from raw context."""
    prompt = build_prompt(raw_context)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SUMMARY_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=TenderSummary,
            temperature=0.2,
        ),
    )
    return response.parsed


@retry(
    retry=retry_if_exception_type(genai_errors.ServerError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def extract_tender_fields(raw_context: str | None) -> TenderFields:
    """Extracts structured fields directly from raw context."""
    prompt = build_prompt(raw_context)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=FIELD_EXTRACTION_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=TenderFields,
            temperature=0.1,
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
