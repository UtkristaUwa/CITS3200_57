# TenderAI Relevance Determination
# Ver. 1.0.0

"""
Relevance / fit scoring for an already-processed tender:
1. Classification: tags the tender against two taxonomies - focus area (the issue,
   cohort or system) and work type (what is being bought) - each tag scored 1-5,
   and flags whether the main deliverable is out of scope entirely.
2. Fit Scoring: blends the strongest score from each facet into a single 0-100 fit
   score, nudged by how recent the tender is, and floored to near zero when the
   main deliverable is out of scope.
3. Record Enrichment: attaches focus_areas, work_types, fit and fit_reason to the
   processed tender record and passes it on.

Takes the dict returned by tender_processor.process_tender() and returns the same
record with the four fields above added, ready for ingestion.upsert_tender().

Taxonomies, classification guidance, the out-of-scope list and the scoring weights
are all loaded from `tender_processor.cfg` so they can be edited without altering
this code.

Requirements:
    pip install pydantic tenacity google-genai

Environment:
    export GEMINI_API_KEY="your_api_key_here"
    export TENDER_PROCESSOR_CONFIG="/path/to/custom_config.cfg"  # optional
"""
import os
import re
from datetime import datetime, timezone, date
from typing import Optional

from google.genai import types
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from processing.tender_processor import client, is_retryable_error, _parse_lenient_cfg


# ==============================================================================
# Pydantic Output Schemas
# (Field descriptions are dynamically populated from tender_processor.cfg)
# ==============================================================================

class TagAssignment(BaseModel):
    tag_id: str = Field(...)
    score: int = Field(...)


class TenderRelevance(BaseModel):
    focus_areas: list[TagAssignment] = Field(default_factory=list)
    work_types: list[TagAssignment] = Field(default_factory=list)
    out_of_scope: bool = Field(default=False)
    reason: Optional[str] = Field(default=None)


# ==============================================================================
# Configuration Loader & Prompt Manager
# ==============================================================================

_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tender_processor.cfg")
_CONFIG_PATH: str = _DEFAULT_CONFIG_PATH
_LAST_CONFIG_MTIME: Optional[float] = None

# Global configuration variables populated by load_config()
RELEVANCE_MODEL: str = "gemini-2.5-flash"
RELEVANCE_TEMPERATURE: float = 0.1
RELEVANCE_CHAR_LIMIT: int = 60000

FOCUS_AREA_WEIGHT: float = 0.6
WORK_TYPE_WEIGHT: float = 0.4
RECENCY_WEIGHT: float = 8.0
RECENCY_HORIZON_DAYS: int = 120
OUT_OF_SCOPE_FIT_CAP: int = 2
MAX_FOCUS_AREAS: int = 3
MAX_WORK_TYPES: int = 2

FOCUS_AREA_TAGS: list[str] = []
WORK_TYPE_TAGS: list[str] = []
RELEVANCE_SYSTEM_INSTRUCTION: str = ""
PROMPT_TEMPLATES: dict[str, str] = {}

# A taxonomy entry opens with "tag_id | Human readable label"; the lines under it
# are prose for the model only.
_TAG_DEFINITION = re.compile(r"^([a-z][a-z0-9_]*)\s*\|\s*\S")


def _parse_tag_ids(block: str) -> list[str]:
    """Pull the allowed tag_ids out of a taxonomy block, so adding a tag to the
    config is all that's needed to make it selectable."""
    return [
        match.group(1)
        for match in (_TAG_DEFINITION.match(line.strip()) for line in block.splitlines())
        if match
    ]


def load_config(config_path: Optional[str] = None):
    """
    Loads the relevance model, taxonomies, prompts and fit scoring weights from an
    external .cfg file. Updates module-level globals and re-binds Pydantic schema
    field descriptions.
    """
    global _CONFIG_PATH, _LAST_CONFIG_MTIME
    global RELEVANCE_MODEL, RELEVANCE_TEMPERATURE, RELEVANCE_CHAR_LIMIT
    global FOCUS_AREA_WEIGHT, WORK_TYPE_WEIGHT, RECENCY_WEIGHT, RECENCY_HORIZON_DAYS
    global OUT_OF_SCOPE_FIT_CAP, MAX_FOCUS_AREAS, MAX_WORK_TYPES
    global FOCUS_AREA_TAGS, WORK_TYPE_TAGS, RELEVANCE_SYSTEM_INSTRUCTION, PROMPT_TEMPLATES

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

    # 1. Model and hyperparameters
    if cfg.has_section("models"):
        RELEVANCE_MODEL = cfg.get("models", "relevance_model", fallback=RELEVANCE_MODEL)
        RELEVANCE_TEMPERATURE = cfg.getfloat("models", "relevance_temperature", fallback=RELEVANCE_TEMPERATURE)
        RELEVANCE_CHAR_LIMIT = cfg.getint("models", "relevance_char_limit", fallback=RELEVANCE_CHAR_LIMIT)

    # 2. Fit scoring weights
    if cfg.has_section("relevance_scoring"):
        FOCUS_AREA_WEIGHT = cfg.getfloat("relevance_scoring", "focus_area_weight", fallback=FOCUS_AREA_WEIGHT)
        WORK_TYPE_WEIGHT = cfg.getfloat("relevance_scoring", "work_type_weight", fallback=WORK_TYPE_WEIGHT)
        RECENCY_WEIGHT = cfg.getfloat("relevance_scoring", "recency_weight", fallback=RECENCY_WEIGHT)
        RECENCY_HORIZON_DAYS = cfg.getint("relevance_scoring", "recency_horizon_days", fallback=RECENCY_HORIZON_DAYS)
        OUT_OF_SCOPE_FIT_CAP = cfg.getint("relevance_scoring", "out_of_scope_fit_cap", fallback=OUT_OF_SCOPE_FIT_CAP)
        MAX_FOCUS_AREAS = cfg.getint("relevance_scoring", "max_focus_areas", fallback=MAX_FOCUS_AREAS)
        MAX_WORK_TYPES = cfg.getint("relevance_scoring", "max_work_types", fallback=MAX_WORK_TYPES)

    # 3. Taxonomies and the system instruction assembled from them
    base_instruction = ""
    if cfg.has_section("system_prompts"):
        base_instruction = cfg.get("system_prompts", "relevance", fallback="").strip()

    if cfg.has_section("relevance_prompts"):
        classification_thoughts = cfg.get("relevance_prompts", "classification_thoughts", fallback="").strip()
        focus_area_block = cfg.get("relevance_prompts", "focus_areas", fallback="").strip()
        work_type_block = cfg.get("relevance_prompts", "work_types", fallback="").strip()
        out_of_scope_block = cfg.get("relevance_prompts", "out_of_scope", fallback="").strip()

        FOCUS_AREA_TAGS.clear()
        FOCUS_AREA_TAGS.extend(_parse_tag_ids(focus_area_block))
        WORK_TYPE_TAGS.clear()
        WORK_TYPE_TAGS.extend(_parse_tag_ids(work_type_block))

        blocks = [
            base_instruction,
            classification_thoughts,
            focus_area_block,
            work_type_block,
            out_of_scope_block,
        ]
        RELEVANCE_SYSTEM_INSTRUCTION = "\n\n".join(b for b in blocks if b)
    else:
        RELEVANCE_SYSTEM_INSTRUCTION = base_instruction

    # 4. Field Descriptions (classification instructions for the Pydantic schemas)
    if cfg.has_section("field_descriptions"):
        descriptions = dict(cfg.items("field_descriptions"))

        if "fit_tag_id" in descriptions:
            TagAssignment.model_fields["tag_id"].description = descriptions["fit_tag_id"]
        if "fit_tag_score" in descriptions:
            TagAssignment.model_fields["score"].description = descriptions["fit_tag_score"]
        TagAssignment.model_rebuild(force=True)

        for field_name, cfg_key in (
            ("focus_areas", "fit_focus_areas"),
            ("work_types", "fit_work_types"),
            ("out_of_scope", "fit_out_of_scope"),
            ("reason", "fit_reason"),
        ):
            if cfg_key in descriptions:
                TenderRelevance.model_fields[field_name].description = descriptions[cfg_key]
        TenderRelevance.model_rebuild(force=True)

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
# Prompt Construction
# ==============================================================================

_NOT_STATED = "(not stated)"


def _describe_value(tender: dict) -> str:
    """The tender's contract value as one line, since the model reads it as prose."""
    amount = tender.get("value_amount")
    currency = tender.get("value_currency")
    notes = tender.get("value_notes")

    parts = []
    if amount is not None:
        parts.append(f"{amount:,.2f} {currency}" if currency else f"{amount:,.2f}")
    if notes:
        parts.append(str(notes))
    return " - ".join(parts) if parts else _NOT_STATED


def build_document_analysis(tender: dict) -> str:
    """
    Concatenates the extracted text of the tender's documents, truncated to
    RELEVANCE_CHAR_LIMIT so a large attachment set can't blow the context window.
    """
    doc_template = PROMPT_TEMPLATES.get(
        "relevance_document", "=== DOCUMENT: {file_name} ===\n{extracted_text}"
    )

    parts = []
    used = 0
    for doc in tender.get("documents") or []:
        text = (doc.get("extracted_text") or "").strip()
        if not text:
            continue
        remaining = RELEVANCE_CHAR_LIMIT - used
        if remaining <= 0:
            break
        block = doc_template.format(
            file_name=doc.get("file_name") or "(unnamed)", extracted_text=text[:remaining]
        )
        used += len(text[:remaining])
        parts.append(block)

    if not parts:
        return PROMPT_TEMPLATES.get(
            "relevance_no_documents",
            "(no document text available - classify from the fields above)",
        )
    return "\n\n".join(parts)


def build_relevance_prompt(tender: dict) -> str:
    """Renders the processed tender record into the classification user prompt."""
    template = PROMPT_TEMPLATES.get(
        "relevance_user_prompt",
        "## Opportunity\n\nTitle: {title}\nBuyer: {issuing_agency}\n\n"
        "## Description\n\n{description}\n\n## Document analysis\n\n{document_analysis}",
    )
    return template.format(
        title=tender.get("title") or _NOT_STATED,
        issuing_agency=tender.get("issuing_agency") or _NOT_STATED,
        category=tender.get("category") or _NOT_STATED,
        location=tender.get("location") or _NOT_STATED,
        value=_describe_value(tender),
        publish_date=tender.get("publish_date") or _NOT_STATED,
        closing_date=tender.get("closing_date") or _NOT_STATED,
        summary_headline=tender.get("summary_headline") or _NOT_STATED,
        description=tender.get("description") or _NOT_STATED,
        document_analysis=build_document_analysis(tender),
    )


# ==============================================================================
# AI Classification Call
# ==============================================================================

@retry(
    retry=retry_if_exception(is_retryable_error),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def classify_tender(tender: dict) -> TenderRelevance:
    """Tags a processed tender against the focus area and work type taxonomies."""
    _check_auto_reload()
    prompt = build_relevance_prompt(tender)

    response = client.models.generate_content(
        model=RELEVANCE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=RELEVANCE_SYSTEM_INSTRUCTION,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            response_mime_type="application/json",
            response_schema=TenderRelevance,
            temperature=RELEVANCE_TEMPERATURE,
        ),
    )
    return response.parsed


# ==============================================================================
# Fit Scoring
# ==============================================================================

def normalise_assignments(
    assignments: list[TagAssignment] | None, allowed_tags: list[str], max_tags: int
) -> dict[str, int]:
    """
    Turns the model's tag list into a {tag_id: score} map: drops anything outside
    the configured taxonomy, clamps scores to 1-5, and keeps only the highest
    scoring tags up to max_tags.
    """
    scores: dict[str, int] = {}
    for assignment in assignments or []:
        tag_id = (assignment.tag_id or "").strip().lower()
        if tag_id not in allowed_tags:
            continue
        score = max(1, min(5, int(assignment.score)))
        if score > scores.get(tag_id, 0):
            scores[tag_id] = score

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return dict(ranked[:max_tags])


def _parse_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).split("T")[0].strip()).date()
    except ValueError:
        return None


def _freshness(tender: dict) -> float:
    """
    How recent this opportunity is, 0.0-1.0: 1.0 published today, 0.0 once it is
    past the recency horizon or has already closed. An unknown publish date sits
    at 0.5 so a missing date neither helps nor hurts.
    """
    today = datetime.now(timezone.utc).date()

    closing_date = _parse_date(tender.get("closing_date"))
    if closing_date and closing_date < today:
        return 0.0

    publish_date = _parse_date(tender.get("publish_date"))
    if publish_date is None:
        return 0.5
    if RECENCY_HORIZON_DAYS <= 0:
        return 0.5

    days_old = (today - publish_date).days
    if days_old <= 0:
        return 1.0
    return max(0.0, 1.0 - days_old / RECENCY_HORIZON_DAYS)


def compute_fit(
    focus_areas: dict[str, int],
    work_types: dict[str, int],
    out_of_scope: bool,
    tender: dict,
) -> int:
    """
    A single 0-100 fit score built from the strongest tag in each facet, then
    nudged by recency. An opportunity that fits neither facet scores 0, and one
    whose main deliverable is out of scope is capped near 0 no matter how well it
    tagged.
    """
    focus_component = max(focus_areas.values(), default=0) / 5
    work_component = max(work_types.values(), default=0) / 5

    blend = FOCUS_AREA_WEIGHT * focus_component + WORK_TYPE_WEIGHT * work_component
    if blend <= 0:
        return 0

    # The tags earn everything except the points reserved for recency, so a
    # freshly published perfect match reaches 100 and nothing gets clipped.
    base = (100 - RECENCY_WEIGHT) * blend
    fit = int(round(max(0.0, min(100.0, base + RECENCY_WEIGHT * _freshness(tender)))))

    if out_of_scope:
        fit = min(fit, OUT_OF_SCOPE_FIT_CAP)
    return fit


# ==============================================================================
# Pipeline Entry Point
# ==============================================================================

def determine_relevance(tender: dict) -> dict:
    """
    Classifies a processed tender record (as returned by process_tender) and
    returns it with the relevance fields attached:

        focus_areas   {focus_area_tag: score 1-5}, or None if nothing applied
        work_types    {work_type_tag: score 1-5}, or None if nothing applied
        fit           overall fit score, 0-100
        fit_reason    1-2 sentences citing the text that drove the decision
    """
    _check_auto_reload()

    relevance = classify_tender(tender)
    focus_areas = normalise_assignments(relevance.focus_areas, FOCUS_AREA_TAGS, MAX_FOCUS_AREAS)
    work_types = normalise_assignments(relevance.work_types, WORK_TYPE_TAGS, MAX_WORK_TYPES)
    fit = compute_fit(focus_areas, work_types, relevance.out_of_scope, tender)

    return {
        **tender,
        "focus_areas": focus_areas or None,
        "work_types": work_types or None,
        "fit": fit,
        "fit_reason": (relevance.reason or "").strip() or None,
    }
