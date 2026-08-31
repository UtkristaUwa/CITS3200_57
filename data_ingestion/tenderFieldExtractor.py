# TenderAI Field Extractor
# Ver. 0.3.0

"""
Separate to the summaryEngine.py which fills the rest of the DB row for a tender.
Reuses the same client/model/document-mapping from summaryEngine.

source_id (which portal this came from) is not extracted here

Fields solved by the summaryEngine:
    description - AI summary of the tender ala gemini
    last_scanned - Datetime of last scan

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
    gather_relevant_documents,
    build_tender_context,
    list_tender_documents,
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
    source_reference_id: Optional[str] = Field(
        description=(
            "The tender's own reference number/ID as stated in the source "
            "(e.g. an ATM ID or tender number like '1222692568' or '26-0084'). None if "
            "not stated."
        )
    )
    source_url: Optional[str] = Field(
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


FIELD_EXTRACTION_SYSTEM_INSTRUCTION = """You are extracting structured data
from raw scraped text documents of an Australian government tender (landing page
and any relevant attachments) for insertion into a database.

Rules:
- Every field must come directly from the source material. Never infer,
  estimate, or calculate a value that isn't explicitly stated - this
  matters most for the value fields and the dates. If it's not there,
  leave it None.
- title/category/status/source_reference_id/source_url normally live on
  the tender's landing page / notice header, but pull them from wherever
  they appear.
- Detail URL: Look for lines like "Detail URL :" or direct links to the tender notice.
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

# ===
# API Call
# =========

@retry(
    retry=retry_if_exception_type(genai_errors.ServerError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def extract_tender_fields(raw_context: str | None) -> TenderFields:
    prompt = build_prompt(raw_context)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=FIELD_EXTRACTION_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=TenderFields,
            temperature=0.1,  # data extraction purposes so lower than the summary engine
        ),
    )

    fields = response.parsed
    # Model is instructed to only pick from TAG_TAXONOMY but it might drift off so we filter here
    fields.tags = [t for t in fields.tags if t in TAG_TAXONOMY]
    return fields


def build_db_record(documents_dir: str) -> dict:
    """
    documents_dir: a tender's directory of .txt files.

    1. Triages and drops irrelevant files, keeping raw text of relevant ones.
    2. Runs summary + field extraction directly from the raw context of relevant files.
    3. Builds the raw 'documents' list for DB storage and stamps last_scanned.

    source_id is left None - which portal a tender came from isn't in the
    document text itself, so that has to be filled in by whatever calls
    this (or added afterwards).
    """
    relevant_docs = gather_relevant_documents(documents_dir)
    raw_context = build_tender_context(relevant_docs)
    documents = list_tender_documents(documents_dir)

    summary = summarise_tender(raw_context)
    fields = extract_tender_fields(raw_context)

    return {
        "source_id": None,
        "source_reference_id": fields.source_reference_id,
        "source_url": fields.source_url,
        "title": fields.title,
        "issuing_agency": fields.issuing_agency,
        "category": fields.category,
        "status": fields.status,
        "publish_date": fields.publish_date,
        "closing_date": fields.closing_date,
        "value_amount": fields.value_amount,
        "value_currency": fields.value_currency,
        "value_notes": fields.value_notes,
        "location": fields.location,
        "description": summary.description,
        "tags": fields.tags,
        "contact_name": fields.contact_name,
        "contact_email": fields.contact_email,
        "contact_phone": fields.contact_phone,
        "lodgment_address": fields.lodgment_address,
        "documents": documents,
        "raw_extra": None,
        "last_scanned": datetime.now(timezone.utc).isoformat(),
    }


# Dummy hardcoded run to test output
if __name__ == "__main__":
    record = build_db_record("tenders_data/26-0084")
    for k, v in record.items():
        print(f"{k}: {v}")
