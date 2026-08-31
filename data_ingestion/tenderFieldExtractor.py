# TenderAI Field Extractor
# Ver. 0.1.0

"""
Separate to the summaryEngine.py which fills the rest of the DB row for a tender. 
Reuses the same client/model/document-mapping from summaryEngine

Fields we are currently covering: publish_date, closing_date, posting_party, value,
location, tags, contact_lodgement_address.

Fields solved by the summaryEngine:
    description - AI summary of the tender ala gemini
    last_scanned - should be handled elsewhere? idk TODO correctly

Setup: see setup instructions 4 the summaryEngine.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from summaryEngine import (
    client,
    MODEL,
    types,
    genai_errors,
    build_prompt,
    summarise_tender,
    gather_document_facts,
)

# ===
# Tag taxonomy
# TODO: placeholder... replace with the real list once we figure out what exactly (or figure out how to dynamically make one)
# Extraction is instructed to only pick from this list, and anything the model returns outside it gets filtered out
# =========

TAG_TAXONOMY = [
    "construction",
    "IT & software",
    "professional services",
    "supply of goods",
    "maintenance",
    "consulting",
]

# ===
# Output Schema
# =========

class TenderFields(BaseModel):
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
    posting_party: Optional[str] = Field(
        description="The agency/department/organisation running the tender. None if genuinely unclear."
    )
    value: Optional[str] = Field(
        description=(
            "Contract value EXACTLY as stated in the source (e.g. '$2.3M', "
            "'$500,000 - $1,000,000 AUD'). Never estimate, calculate, or "
            "infer a value that isn't explicitly written somewhere in the "
            "material — if no value is stated, this MUST be None."
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
    contact_lodgement_address: Optional[str] = Field(
        description=(
            "Contact email/address for enquiries and/or the lodgement "
            "address for submitting a response. If the source explicitly "
            "says this isn't available yet (e.g. 'refer to ATM documents "
            "once released'), record that statement rather than leaving it "
            "blank — that's still real information, different from it just "
            "not being mentioned at all. Only use None if the source says "
            "nothing about contact/lodgement whatsoever."
        )
    )


FIELD_EXTRACTION_SYSTEM_INSTRUCTION = """You are extracting structured data
from an Australian government tender notice (plus any attached documents,
already reduced to compact facts) for insertion into a database.

Rules:
- Every field must come directly from the source material. Never infer,
  estimate, or calculate a value that isn't explicitly stated — this
  matters most for `value` and the dates. If it's not there, leave it None.
- Distinguish "not stated anywhere" (-> None) from "explicitly stated as
  not yet available" (-> record that statement). Both are different from
  guessing, and both are useful, just not the same thing.
- Dates: normalise to ISO 8601. Include time and timezone offset for
  closing_date only if the source actually gives them.
- Tags: only ever choose from the provided list. If nothing fits well,
  return an empty list rather than forcing a loose match.
"""

# ===
# API Call
# =========

# TODO: same as in the summary engine need to figure out what the optimal set up for this is
# Shouldnt be too pressing since time isnt really a constraint here
@retry(
    retry=retry_if_exception_type(genai_errors.ServerError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def extract_tender_fields(notice_fields: dict, attached_documents: str | None = None) -> TenderFields:
    prompt = build_prompt(notice_fields, attached_documents)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=FIELD_EXTRACTION_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=TenderFields,
            temperature=0.1,  # data extraction, not writing — go lower than the summary call
        ),
    )

    fields = response.parsed
    # safety net — model is instructed to only pick from TAG_TAXONOMY but
    # nothing stops it drifting, so filter rather than trust blindly
    fields.tags = [t for t in fields.tags if t in TAG_TAXONOMY]
    return fields

# TODO: make it into the model kris gave me 
def build_db_record(notice_fields: dict, documents_dir: str | None = None) -> dict:
    """
    Runs the summary engine + field extraction off the SAME mapped
    document facts (only maps documents once), then shld grab last_scanned date/time
    itself since that's not up to gemini (for now).
    """
    combined_facts = gather_document_facts(documents_dir) if documents_dir else None

    summary = summarise_tender(notice_fields, combined_facts)
    fields = extract_tender_fields(notice_fields, combined_facts)

    return {
        "publish_date": fields.publish_date,
        "closing_date": fields.closing_date,
        "posting_party": fields.posting_party,
        "value": fields.value,
        "location": fields.location,
        "description": summary.description,
        "tags": fields.tags,
        "last_scanned": datetime.now(timezone.utc).isoformat(),
        "contact_lodgement_address": fields.contact_lodgement_address,
    }


# Dummy hardcoded run to test output
if __name__ == "__main__":

    notice_fields = {
        "ATM ID": "Notice 2 - 25/3151",
        "Title": "Notice of Intended Procurement - Bulk Fuel Farm",
        "Agency": "Department of Climate Change, Energy, the Environment and Water",
        "Category": "24110000 - Containers and storage",
        "Close Date & Time": "18-Aug-2026 2:00 pm (ACT Local Time)",
        "Publish Date": "21-Jul-2026",
        "Location": "ACT, NSW, VIC, SA, WA, QLD, NT, TAS",
        "ATM Type": "Notice",
        "Description": (
            "This is a pre-release notice only to provide advance notification "
            "that the Australian Antarctic Division (AAD) of the Department of "
            "Climate Change, Energy, the Environment and Water anticipates that "
            "it will commence a single stage, open tender process in Q3 2026 for "
            "the supply of components for a Special Antarctic Blend (SAB) Bulk "
            "Storage Infrastructure to support the Macquarie Island Station "
            "Project (MISP)."
        ),
    }

    record = build_db_record(notice_fields, documents_dir="tenders/25-3151")
    for k, v in record.items():
        print(f"{k}: {v}")
