"""
Building the ingestion record for one scraped tender.

Each tender directory gets a `tender.json` in exactly the shape defined by
ingestion/sample_tender.json and validated by ingestion/tender.schema.json,
so a scrape feeds straight into ingestion/validate_and_submit.py with no
reshaping in between.

That schema sets "additionalProperties": false, so nothing extra can be bolted
on at the top level. Everything the scraper knows that the schema does not
model -- which documents were downloadable, where each file landed, whether the
portal demanded a login -- goes in `raw_extra`, which is exactly what it is for.
The `documents` array carries only the four keys the schema permits;
`extracted_text` is left null for the document-extraction stage to fill in.
"""

import re
from datetime import date, datetime

# The schema's enums. Anything we cannot confidently map becomes None rather
# than a guess: a wrong enum value is worse than an absent one, because it
# silently misfiles the tender instead of failing validation.
CATEGORIES = ("tender", "rfq", "eoi", "grant")
STATUSES = ("open", "closed", "awarded", "unknown")
FILE_TYPES = ("pdf", "docx", "rtf", "other")

# How each portal words its tender types.
CATEGORY_PATTERNS = (
    ("eoi", r"expression\s+of\s+interest|\beoi\b|invitation\s+to\s+register|registration\s+of\s+interest|\bitr\b"),
    ("rfq", r"request\s+for\s+quot|\brfq\b"),
    ("grant", r"\bgrant\b"),
    ("tender", r"request\s+for\s+tender|\brft\b|approach\s+to\s+market|\batm\b|tender|"
                r"request\s+for\s+proposal|\brfp\b|invitation\s+to\s+offer|\bito\b"),
)

STATUS_PATTERNS = (
    ("awarded", r"\bawarded\b|contract\s+awarded"),
    ("closed", r"\bclosed\b|\bcompleted\b|no\s+longer\s+accepting"),
    ("open", r"\bopen\b|\bcurrent\b|accepting"),
)

# Date formats these portals actually print, in the order we try them.
DATE_FORMATS = (
    "%d-%b-%Y",      # 17-Aug-2026   (AusTender)
    "%d/%b/%Y",      # 23/Sep/2026   (VendorPanel)
    "%d/%m/%Y",      # 23/09/2026
    "%d %B %Y",      # 1 March 2030  (Buying for Victoria)
    "%d %b %Y",      # 1 Mar 2030
    "%Y-%m-%d",      # already ISO
)


def classify_category(*values):
    """Map a portal's tender-type wording onto the schema's category enum."""
    haystack = " ".join(v for v in values if v).lower()
    for category, pattern in CATEGORY_PATTERNS:
        if re.search(pattern, haystack):
            return category
    return None


def classify_status(*values):
    """Map a portal's status wording onto the schema's status enum."""
    haystack = " ".join(v for v in values if v).lower()
    for status, pattern in STATUS_PATTERNS:
        if re.search(pattern, haystack):
            return status
    return None


def classify_file_type(file_name):
    """Map a filename's extension onto the schema's file_type enum."""
    suffix = (file_name or "").rsplit(".", 1)
    if len(suffix) != 2:
        return "other"
    extension = suffix[1].lower()
    if extension in ("pdf",):
        return "pdf"
    if extension in ("docx", "doc"):
        return "docx"
    if extension in ("rtf",):
        return "rtf"
    return "other"


def parse_date(value):
    """
    Pull an ISO date out of whatever a portal printed, or return None.

    Portal date cells carry trailing noise ("17-Aug-2026 2:00 pm (ACT Local
    Time) Show close time for other time zones"), so the date is matched inside
    the string rather than parsed from the whole of it.
    """
    if not value:
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")

    text = str(value).strip()
    candidates = re.findall(
        r"\d{4}-\d{2}-\d{2}|\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4}",
        text,
    )
    for candidate in candidates or [text]:
        cleaned = candidate.strip()
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def parse_money(value):
    """Return a plain number for a printed amount, or None if it is not one."""
    if value is None or isinstance(value, (int, float)):
        return value
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def first_email(*values):
    """First email address appearing in any of the given strings."""
    for value in values:
        if not value:
            continue
        match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", str(value))
        if match:
            return match.group().rstrip(".")
    return None


def clean_text(value):
    """Collapse scraped whitespace; the schema wants plain text, not markup."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def document_entries(documents):
    """
    The schema-permitted view of the attachments.

    Only the four keys the schema allows appear here. `extracted_text` stays
    null: pulling text out of the files is the document-extraction stage's job,
    and it fills this in before submission.
    """
    return [
        {
            "document_id": None,
            "file_name": document.file_name,
            "file_type": classify_file_type(document.file_name),
            "extracted_text": None,
            "parsed_at": None,
        }
        for document in documents
    ]


def download_provenance(documents, requires_login):
    """
    What the scraper knows about the attachments that the schema does not model.

    Kept under raw_extra so a consumer can tell an empty `documents` array
    meaning "this tender has no attachments" from one meaning "the portal would
    not give them to us", and can find the downloaded file on disk.
    """
    return {
        "documents_advertised": len(documents),
        "documents_downloaded": sum(1 for d in documents if d.downloaded),
        "documents_require_login": requires_login,
        "documents_detail": [
            {
                "file_name": document.file_name,
                "url": document.url,
                "version": document.version,
                "size_label": document.size_label,
                "downloaded": document.downloaded,
                "local_path": document.local_path,
                "bytes_written": document.bytes_written,
                "error": document.error,
            }
            for document in documents
        ],
    }


def build_record(
    source_id,
    source_url,
    title,
    reference=None,
    issuing_agency=None,
    category=None,
    status=None,
    publish_date=None,
    closing_date=None,
    value_amount=None,
    value_currency=None,
    value_notes=None,
    location=None,
    description=None,
    contact_name=None,
    contact_email=None,
    contact_phone=None,
    lodgment_address=None,
    documents=(),
    requires_login=False,
    raw_extra=None,
):
    """
    Assemble one tender in ingestion's 20-field shape.

    `title` falls back to the reference: the schema requires a non-empty title,
    and dropping an entire tender because a portal rendered its heading oddly
    would be a poor trade.
    """
    documents = list(documents)
    extra = dict(raw_extra or {})
    extra["scrape"] = download_provenance(documents, requires_login)

    return {
        "source_id": source_id,
        "source_reference_id": reference or None,
        "source_url": source_url,
        "title": clean_text(title) or (reference or "Untitled tender"),
        "issuing_agency": clean_text(issuing_agency),
        "category": category if category in CATEGORIES else None,
        "status": status if status in STATUSES else None,
        "publish_date": parse_date(publish_date),
        "closing_date": parse_date(closing_date),
        "value_amount": parse_money(value_amount),
        "value_currency": value_currency,
        "value_notes": clean_text(value_notes),
        "location": clean_text(location),
        "description": clean_text(description),
        "contact_name": clean_text(contact_name),
        "contact_email": contact_email,
        "contact_phone": clean_text(contact_phone),
        "lodgment_address": clean_text(lodgment_address),
        "documents": document_entries(documents),
        "raw_extra": extra,
    }
