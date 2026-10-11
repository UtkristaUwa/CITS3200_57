import json
import logging
import math
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import StreamingResponse

from app import runtime_config
from app.auth import current_user
from app.bigquery import (
    get_client,
    get_locations,
    get_sources,
    get_storage_client,
    get_tender_documents,
    list_tenders,
)
from app.config import settings
from app.document_zip import (
    ArchiveEntry,
    archive_filename,
    dedupe_names,
    entry_name,
    parse_storage_uri,
    stream_zip,
)
from app.models import SourceOut, TenderOut

_ALLOWED_BUCKET_PREFIX = "tenderai-"
logger = logging.getLogger(__name__)

router = APIRouter()

# Mirrors the enums documented in ingestion/tender.schema.json — invalid
# values 422 automatically instead of silently matching nothing.
StatusFilter = Literal["open", "closed", "awarded", "unknown"]
CategoryFilter = Literal["tender", "rfq", "eoi", "grant"]
SearchMode = Literal["keyword", "semantic"]
# Mirrors ALLOWED_LOCATION_STATES in processing/tender_processor.py.
StateFilter = Literal["WA", "NSW", "VIC", "QLD", "SA", "TAS", "ACT", "NT", "NATIONAL"]
SortDirection = Literal["asc", "desc"]

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


def _parse_classification_scores(value: object) -> dict[str, int | float]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError, RecursionError):
            return {}

    if not isinstance(value, dict):
        return {}

    scores: dict[str, int | float] = {}
    for tag_id, score in value.items():
        if not isinstance(tag_id, str) or not tag_id:
            continue
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        if not 1 <= score <= 5 or not math.isfinite(score):
            continue
        scores[tag_id] = score
    return scores


def _classification_tags(value: object, labels: dict[str, str]) -> list[dict[str, str]]:
    scores = _parse_classification_scores(value)
    ordered = sorted(
        ((tag_id, score) for tag_id, score in scores.items() if tag_id in labels),
        key=lambda item: (-item[1], item[0]),
    )
    return [{"id": tag_id, "label": labels[tag_id]} for tag_id, _score in ordered]


def _tender_outputs(
    rows: list[dict],
    taxonomies: runtime_config.RuntimeTaxonomies | None = None,
) -> list[TenderOut]:
    focus_area_labels = taxonomies.focus_areas if taxonomies else {}
    work_type_labels = taxonomies.work_types if taxonomies else {}
    outputs: list[TenderOut] = []

    for row in rows:
        values = dict(row)
        values["focus_areas"] = _classification_tags(
            values.get("focus_areas"),
            focus_area_labels,
        )
        values["work_types"] = _classification_tags(
            values.get("work_types"),
            work_type_labels,
        )
        outputs.append(TenderOut(**values))

    return outputs


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

    row_pub_str = row.get("publish_date")
    row_pub = None
    if isinstance(row_pub_str, date):
        row_pub = row_pub_str
    elif row_pub_str:
        row_pub = date.fromisoformat(str(row_pub_str)) if row_pub_str else None

    if published_before:
        if row_pub is None or row_pub > published_before:
            return False
    if published_after:
        if row_pub is None or row_pub < published_after:
            return False
             
    if year:
        closing_date = str(row.get("closing_date") or "")
        publish_date = str(row.get("publish_date") or "")
        if not (closing_date.startswith(year) or publish_date.startswith(year)):
            return False

    if q:
        tokens = q.strip().lower().split()
        haystack = f"{row.get('title') or ''} {row.get('source_reference_id') or ''} {row.get('description') or ''} {row.get('summary_headline') or ''} {row.get('issuing_agency') or ''}".lower()
        if not all(token in haystack for token in tokens):
            return False
    return True


def _sort_mock_tenders(rows: list[dict], sort_by: str | None, sort_dir: str | None) -> list[dict]:
    if not sort_by:
        return rows
    is_asc = (sort_dir or "").lower() == "asc"
    def sort_key(row):
        val = row.get(sort_by)
        if val is None:
            return (1, "") if is_asc else (-1, "")
        return (0, str(val))
    return sorted(rows, key=sort_key, reverse=not is_asc)


@router.get("/sources", response_model=list[SourceOut])
def get_sources_endpoint() -> list[SourceOut]:
    """
    Return all distinct tender source portals.
    """
    if settings.use_mock_data:
        seen = {}
        for row in _MOCK_TENDERS:
            sid = row.get("source_id")
            if sid and sid not in seen:
                seen[sid] = SourceOut(source_id=sid, name=sid.replace("-", " ").title())
        return list(seen.values())

    client = get_client()
    raw = get_sources(client)
    return [SourceOut(source_id=r["source_id"], name=r["name"]) for r in raw]


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
    published_before: date | None = Query(default=None, description="Published on or before this date"),
    published_after: date | None = Query(default=None, description="Published on or after this date"),
    sort_by: str | None = Query(default=None, description="Sort field (e.g. closing_date, publish_date, title)"),
    sort_dir: SortDirection | None = Query(default=None, description="Sort direction: 'asc' or 'desc'"),
    year: str | None = Query(
        default=None,
        pattern=r"^[0-9]{4}$",
        description="Year of closing or publish date",
    ),
    q: str | None = Query(default=None, min_length=1, max_length=200, description="Keyword search"),
    mode: SearchMode = Query(
        default="keyword",
        description="Search mode: 'keyword' (fast token search) or 'semantic' (AI vector search)",
    ),
    states: list[StateFilter] | None = Query(
        default=None,
        description="Filter to tenders in any of these states. Repeat the parameter.",
    ),
    include_irrelevant: bool = Query(
        default=False,
        description="Admins only: also return tenders below the fit threshold.",
    ),
    user: dict = Depends(current_user),
) -> list[TenderOut]:
    # Staff never see tenders below min_visible_fit. An admin can opt out of
    # the filter, but it still happens in SQL rather than in the browser:
    # only ~17% of tenders clear the bar, so filtering a 50-row page after the
    # fact would leave about eight cards on it.
    # The flag can only ever loosen the filter, and only for an admin.
    min_fit = (
        None
        if user.get("isAdmin") and include_irrelevant
        else settings.min_visible_fit
    )
    if settings.use_mock_data:
        matches = [row for row in _MOCK_TENDERS if _matches_mock(row, status, category, source_id, location, min_value, max_value, closing_before, closing_after, year, q)]
        matches = _sort_mock_tenders(matches, sort_by, sort_dir)
        page = matches[offset : offset + limit]
        return _tender_outputs(page)

    client = get_client()
    rows = list_tenders(
        client, limit=limit, offset=offset,
        status=status, category=category, source_id=source_id, 
        location=location, min_value=min_value, max_value=max_value, 
        closing_before=closing_before, closing_after=closing_after, 
        published_before=published_before, published_after=published_after,
        year=year, q=q,
        mode=mode, min_fit=min_fit,
        states=list(states) if states else None,
        sort_by=sort_by, sort_dir=sort_dir,
    )
    if not rows:
        return []

    try:
        taxonomies = runtime_config.get_taxonomy_labels()
    except runtime_config.RuntimeConfigError:
        logger.warning("Runtime taxonomy unavailable; returning empty tender classifications")
        taxonomies = None
    return _tender_outputs(rows, taxonomies)


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


@router.get("/tenders/{tender_id}/documents/zip")
def download_all_documents(
    tender_id: str = Path(..., pattern=r"^[A-Za-z0-9._-]{1,128}$"),
) -> StreamingResponse:
    """Every stored attachment of one tender, as a single zip.

    The document list comes from BigQuery rather than the caller, so the
    archive only ever holds files that belong to that tender. Everything that
    can fail is checked before the first byte is sent, while a real status
    code is still possible:

    * no such tender → 404
    * no stored attachments → 404, before any Cloud Storage client exists
    * Cloud Storage unreachable or not listable → 503
    * an attachment BigQuery lists but the bucket doesn't hold → 404 naming it

    A failure after streaming has started is handled in stream_zip().
    """
    if settings.use_mock_data:
        tender = next((row for row in _MOCK_TENDERS if row["tender_id"] == tender_id), None)
    else:
        tender = get_tender_documents(get_client(), tender_id)
    if tender is None:
        raise HTTPException(status_code=404, detail="Tender not found")

    # Documents without a storage_uri (the scraped page text, unprocessed
    # extractions) are pipeline files, not attachments; repeats are one file.
    wanted: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for doc in tender.get("documents") or []:
        location = parse_storage_uri(doc.get("storage_uri"))
        if location is None or location in seen:
            continue
        seen.add(location)
        bucket_name, object_path = location
        wanted.append((entry_name(doc, object_path), bucket_name, object_path))

    if not wanted:
        raise HTTPException(status_code=404, detail="No stored documents for this tender")

    # One listing per folder (normally just the tender's own folder) instead
    # of one metadata request per file. Listed blobs carry the size and
    # timestamp the zip headers need, and a listing proves each object exists
    # under the same service account that will read it.
    stored: dict[tuple[str, str], object] = {}
    try:
        storage = get_storage_client()
        for bucket_name, folder in {(b, p.rpartition("/")[0]) for _, b, p in wanted}:
            for blob in storage.list_blobs(bucket_name, prefix=f"{folder}/" if folder else None):
                stored[(bucket_name, blob.name)] = blob
    except Exception:
        logger.exception("Could not list stored documents for tender %s", tender_id)
        raise HTTPException(
            status_code=503,
            detail="Document storage is unavailable right now. Please try again.",
        )

    missing = [name for name, b, p in wanted if (b, p) not in stored]
    if missing:
        logger.error("Tender %s: %d document(s) missing from storage: %s", tender_id, len(missing), missing)
        shown = ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
        raise HTTPException(status_code=404, detail=f"Missing from document storage: {shown}")

    names = dedupe_names(name for name, _, _ in wanted)
    entries = [ArchiveEntry(name, stored[(b, p)]) for name, (_, b, p) in zip(names, wanted)]
    return StreamingResponse(
        stream_zip(entries, tender_id=tender_id),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{archive_filename(tender)}"'},
    )


@router.get("/locations", response_model=list[str])
def list_locations() -> list[str]:
    if settings.use_mock_data:
        # Extract unique, non-empty locations from the local mock data
        locations = {row.get("location") for row in _MOCK_TENDERS if row.get("location")}
        return sorted(list(locations))
    
    client = get_client()
    return get_locations(client)
