from typing import Literal
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.bigquery import get_client, get_storage_client, list_tenders, get_locations
from app.config import settings
from app.models import TenderOut

_ALLOWED_BUCKET_PREFIX = "tenderai-"

router = APIRouter()

# Mirrors the enums documented in ingestion/tender.schema.json — invalid
# values 422 automatically instead of silently matching nothing.
StatusFilter = Literal["open", "closed", "awarded", "unknown"]
CategoryFilter = Literal["tender", "rfq", "eoi", "grant"]

# Fixture data for USE_MOCK_DATA=true, shaped like ingestion/sample_tender.json
# plus the bookkeeping fields upsert_tender() would normally compute.
_MOCK_TENDERS = [
    {
        "tender_id": "mock-1",
        "source_reference_id": "PROCF22-000236",
        "source_id": "vic-buyingfor",
        "source_url": "https://www.tenders.vic.gov.au/tenders/tender/display?id=PROCF22-000236",
        "title": "Provision of Independent Evaluation Services",
        "issuing_agency": "Department of Families, Fairness and Housing",
        "category": "tender",
        "status": "open",
        "publish_date": "2026-08-01",
        "closing_date": "2026-09-15",
        "value_amount": 250000,
        "value_currency": "AUD",
        "value_notes": None,
        "location": "Victoria",
        "summary_headline": "DFFH seeks an independent evaluator for a state-wide early childhood program.",
        "relevance_score": 92,
        "description": (
            "The Department is seeking an experienced provider to conduct an "
            "independent evaluation of a state-wide early childhood program. "
            "The evaluation will assess program outcomes, identify areas for improvement, "
            "and provide recommendations to inform future policy and investment decisions. "
            "Providers must demonstrate experience in evaluation methodology, stakeholder "
            "engagement, and working with government agencies."
        ),
        "contact_name": "Jane Smith",
        "contact_email": "jane.smith@example.vic.gov.au",
        "contact_phone": "+61 3 9000 0000",
        "lodgment_address": None,
        "documents": [
            {
                "file_name": "Request for Tender.pdf",
                "file_type": "pdf",
                "extracted_text": "1. Background\nThe Department is seeking...",
            }
        ],
        "content_hash": "mock",
        "first_seen_at": "2026-08-01T00:00:00Z",
        "last_scanned_at": "2026-08-20T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
        "raw_extra": {"portal_status_label": "Open"},
    },
    {
        "tender_id": "mock-2",
        "source_reference_id": "DOT44341",
        "source_id": "vic-buyingfor",
        "source_url": "https://www.tenders.vic.gov.au/tenders/tender/display?id=DOT44341",
        "title": "Regional Road Maintenance Contract",
        "issuing_agency": "Department of Transport and Planning",
        "category": "tender",
        "status": "open",
        "publish_date": "2026-07-15",
        "closing_date": "2026-09-01",
        "value_amount": None,
        "value_currency": None,
        "value_notes": "$1M-$5M range",
        "location": "Regional Victoria",
        "summary_headline": "DoTP seeks a contractor for ongoing maintenance of regional arterial roads.",
        "relevance_score": 10,
        "description": "Ongoing maintenance works across regional arterial roads.",
        "contact_name": None,
        "contact_email": "procurement@transport.vic.gov.au",
        "contact_phone": None,
        "lodgment_address": None,
        "documents": [],
        "content_hash": "mock",
        "first_seen_at": "2026-07-15T00:00:00Z",
        "last_scanned_at": "2026-08-20T00:00:00Z",
        "updated_at": "2026-07-15T00:00:00Z",
        "raw_extra": None,
    },
]


def _matches_mock(
    row: dict,
    status: str | None,
    category: str | None,
    source_id: str | None,
    location: str | None,
    min_value: float | None,
    max_value: float | None,
    closing_before: date | None,
    closing_after: date | None,
    year: str | None,
    q: str | None
) -> bool:
    if status and row.get("status") != status:
        return False
    if category and row.get("category") != category:
        return False
    if source_id and row.get("source_id") != source_id:
        return False
    
    if location:
        tender_loc = (row.get("location") or "").lower()
        if location.lower() not in tender_loc:
            return False

    row_val = row.get("value_amount")
    if min_value is not None:
        if row_val is None or row_val < min_value:
            return False
    if max_value is not None:
        if row_val is None or row_val > max_value:
            return False

    row_closing_str = row.get("closing_date")
    # Date conversion for mock logic
    if isinstance(row_closing_str, date):
        row_closing = row_closing_str
    else:
        row_closing = date.fromisoformat(str(row_closing_str)) if row_closing_str else None
    
    if closing_before:
        if row_closing is None or row_closing > closing_before:
             return False
    if closing_after:
        if row_closing is None or row_closing < closing_after:
             return False
             
    if year:
        closing_date = str(row.get("closing_date") or "")
        publish_date = str(row.get("publish_date") or "")
        if not (closing_date.startswith(year) or publish_date.startswith(year)):
            return False

    if q:
        needle = q.lower()
        haystack = f"{row.get('title') or ''} {row.get('description') or ''}".lower()
        if needle not in haystack:
            return False
    return True


@router.get("/tenders", response_model=list[TenderOut])
def get_tenders(
    limit: int = Query(default=50, le=200, gt=0),
    offset: int = Query(default=0, ge=0),
    status: StatusFilter | None = Query(default=None, description="Exact match on tender status"),
    category: CategoryFilter | None = Query(default=None, description="Exact match on tender category"),
    source_id: str | None = Query(default=None, description="Exact match on portal"),
    location: str | None = Query(default=None, description="Partial match on jurisdiction/location"),
    min_value: float | None = Query(default=None, description="Minimum tender value"),
    max_value: float | None = Query(default=None, description="Maximum tender value"),
    closing_before: date | None = Query(default=None, description="Closing on or before this date"),
    closing_after: date | None = Query(default=None, description="Closing on or after this date"),
    year: str | None = Query(
        default=None,
        pattern=r"^[0-9]{4}$",
        description="Year of closing or publish date",
    ),
    q: str | None = Query(default=None, min_length=1, max_length=200, description="Keyword search"),
) -> list[TenderOut]:
    if settings.use_mock_data:
        matches = [row for row in _MOCK_TENDERS if _matches_mock(row, status, category, source_id, location, min_value, max_value, closing_before, closing_after, year, q)]
        page = matches[offset : offset + limit]
        return [TenderOut(**row) for row in page]

    client = get_client()
    rows = list_tenders(
        client, limit=limit, offset=offset,
        status=status, category=category, source_id=source_id, 
        location=location, min_value=min_value, max_value=max_value, 
        closing_before=closing_before, closing_after=closing_after, year=year, q=q,
    )
    return [TenderOut(**row) for row in rows]
@router.get("/documents/download")
def download_document(
    storage_url: str = Query(..., description="HTTPS GCS URL from a TenderDocument"),
    filename: str = Query(..., max_length=500, description="Filename for Content-Disposition"),
) -> StreamingResponse:
    prefix = "https://storage.googleapis.com/"
    if not storage_url.startswith(prefix):
        raise HTTPException(status_code=400, detail="Invalid storage URL")
    path = storage_url[len(prefix):]
    parts = path.split("/", 1)
    if len(parts) != 2 or not parts[0].startswith(_ALLOWED_BUCKET_PREFIX):
        raise HTTPException(status_code=400, detail="Invalid storage URL")
    bucket_name, blob_path = parts

    client = get_storage_client()
    blob = client.bucket(bucket_name).blob(blob_path)

    try:
        data = blob.download_as_bytes()
    except Exception:
        raise HTTPException(status_code=404, detail="File not found or inaccessible")

    safe_name = filename.replace('"', "_")
    blob.reload()
    content_type = blob.content_type or "application/octet-stream"
    return StreamingResponse(
        iter([data]),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/locations", response_model=list[str])
def list_locations() -> list[str]:
    if settings.use_mock_data:
        # Extract unique, non-empty locations from the local mock data
        locations = {row.get("location") for row in _MOCK_TENDERS if row.get("location")}
        return sorted(list(locations))
    
    client = get_client()
    return get_locations(client)
