from typing import Literal

from fastapi import APIRouter, Query

from app.bigquery import get_client, list_tenders
from app.config import settings
from app.models import TenderOut

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
        "description": (
            "The Department is seeking an experienced provider to conduct an "
            "independent evaluation of a state-wide early childhood program."
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


def _matches_mock(row: dict, status: str | None, category: str | None,
                   source_id: str | None, q: str | None) -> bool:
    if status and row["status"] != status:
        return False
    if category and row["category"] != category:
        return False
    if source_id and row["source_id"] != source_id:
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
    source_id: str | None = Query(default=None, description="Exact match on which portal a tender came from"),
    q: str | None = Query(
        default=None, min_length=1, max_length=200,
        description="Case-insensitive keyword search across title and description",
    ),
) -> list[TenderOut]:
    if settings.use_mock_data:
        matches = [row for row in _MOCK_TENDERS if _matches_mock(row, status, category, source_id, q)]
        page = matches[offset : offset + limit]
        return [TenderOut(**row) for row in page]

    client = get_client()
    rows = list_tenders(
        client, limit=limit, offset=offset,
        status=status, category=category, source_id=source_id, q=q,
    )
    return [TenderOut(**row) for row in rows]
