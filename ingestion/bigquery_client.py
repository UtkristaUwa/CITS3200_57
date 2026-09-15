from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from google.cloud import bigquery

PROJECT_ID = "tenderai-dev"
DATASET = "TenderAI"
TENDERS_TABLE = f"{PROJECT_ID}.{DATASET}.tenders"
SNAPSHOTS_TABLE = f"{PROJECT_ID}.{DATASET}.tender_snapshots"

# Fields that make up a tender's *content* for hashing/diffing purposes.
# Deliberately excludes identity fields (source_id/source_reference_id/
# source_url — changing these would usually mean it's a different tender,
# not an edit) and bookkeeping fields (computed, never hashed).
CONTENT_FIELDS = [
    "title",
    "issuing_agency",
    "category",
    "status",
    "publish_date",
    "closing_date",
    "value_amount",
    "value_currency",
    "value_notes",
    "location",
    "description",
    "contact_name",
    "contact_email",
    "contact_phone",
    "lodgment_address",
    "documents",
    "raw_extra",
]

# Columns returned/selected for a tenders row, in one place so the SELECT
# in find_existing() and the row built for a load job stay in sync.
ALL_COLUMNS = [
    "tender_id",
    "source_reference_id",
    "source_id",
    "source_url",
    "title",
    "issuing_agency",
    "category",
    "status",
    "publish_date",
    "closing_date",
    "value_amount",
    "value_currency",
    "value_notes",
    "location",
    "description",
    "contact_name",
    "contact_email",
    "contact_phone",
    "lodgment_address",
    "documents",
    "content_hash",
    "first_seen_at",
    "last_scanned_at",
    "updated_at",
    "raw_extra",
]


def get_client() -> bigquery.Client:
    """Build a BigQuery client using whatever ADC is configured locally."""
    return bigquery.Client(project=PROJECT_ID)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> Any:
    """Normalise a value before hashing so equivalent inputs hash the same."""
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def _document_content_key(doc: dict) -> tuple:
    """The part of a document that counts as *content* for hashing/diffing.

    Deliberately excludes document_id and parsed_at: those are bookkeeping
    that _prepare_documents() regenerates fresh on every submission (a new
    uuid4() and "now" each call), so including them would make the hash of
    an otherwise-identical resubmission come out different every time and
    break the whole insert/update/no-op guarantee.
    """
    return (doc.get("file_name"), doc.get("file_type"), doc.get("extracted_text"))


def _content_view(record: dict) -> dict:
    """Build the content-fields payload used for both hashing and diffing,
    with documents reduced to their content key (see above) and sorted so
    submission order never affects the result."""
    view = {field: _canonical(record.get(field)) for field in CONTENT_FIELDS}
    documents = record.get("documents") or []
    view["documents"] = sorted(
        (_document_content_key(d) for d in documents), key=lambda k: (k[0] or "", k[1] or "")
    )
    return view


def compute_content_hash(record: dict) -> str:
    """SHA-256 over the record's content fields, order-independent."""
    payload = _content_view(record)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _prepare_documents(documents: list | None) -> list:
    """Fill in document_id/parsed_at for any document that's missing them."""
    prepared = []
    for doc in documents or []:
        doc = dict(doc)
        doc.setdefault("document_id", str(uuid.uuid4()))
        doc.setdefault("parsed_at", _now_iso())
        doc.setdefault("file_type", None)
        doc.setdefault("extracted_text", None)
        prepared.append(doc)
    return prepared


def _find_existing(client: bigquery.Client, record: dict) -> dict | None:
    """
    Look up an existing tenders row for this record's identity.

    Matches on (source_id, source_reference_id) when a reference id is
    given, otherwise falls back to (source_id, source_url).
    """
    select_cols = ", ".join(ALL_COLUMNS)
    if record.get("source_reference_id"):
        query = f"""
            SELECT {select_cols}
            FROM `{TENDERS_TABLE}`
            WHERE source_id = @source_id
              AND source_reference_id = @source_reference_id
            LIMIT 1
        """
        params = [
            bigquery.ScalarQueryParameter("source_id", "STRING", record["source_id"]),
            bigquery.ScalarQueryParameter(
                "source_reference_id", "STRING", record["source_reference_id"]
            ),
        ]
    else:
        query = f"""
            SELECT {select_cols}
            FROM `{TENDERS_TABLE}`
            WHERE source_id = @source_id
              AND source_url = @source_url
            LIMIT 1
        """
        params = [
            bigquery.ScalarQueryParameter("source_id", "STRING", record["source_id"]),
            bigquery.ScalarQueryParameter("source_url", "STRING", record["source_url"]),
        ]

    job = client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params))
    rows = list(job.result())
    if not rows:
        return None
    existing = dict(rows[0])
    # BigQuery returns nested STRUCT array elements as Row-like objects,
    # not plain dicts — normalise so _canonical()/_diff_fields() compare
    # like-for-like against the plain-dict documents on the new record.
    if existing.get("documents"):
        existing["documents"] = [dict(d) for d in existing["documents"]]
    return existing


def _json_safe(value: Any) -> Any:
    """Recursively convert datetime objects (as returned by BigQuery query
    results) to ISO strings so the row can round-trip through json.dumps
    in a load job."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _load_row(client: bigquery.Client, row: dict) -> None:
    """Write one full row to `tenders` via a load job (not streaming —
    see module docstring for why)."""
    row = _json_safe(row)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_json([row], TENDERS_TABLE, job_config=job_config)
    job.result()  # raises on failure, with the real BigQuery error


def _delete_row(client: bigquery.Client, tender_id: str) -> None:
    query = f"DELETE FROM `{TENDERS_TABLE}` WHERE tender_id = @tender_id"
    params = [bigquery.ScalarQueryParameter("tender_id", "STRING", tender_id)]
    job = client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params))
    job.result()


def _bump_last_scanned(client: bigquery.Client, tender_id: str) -> None:
    query = f"""
        UPDATE `{TENDERS_TABLE}`
        SET last_scanned_at = @now
        WHERE tender_id = @tender_id
    """
    params = [
        bigquery.ScalarQueryParameter("now", "TIMESTAMP", _now_iso()),
        bigquery.ScalarQueryParameter("tender_id", "STRING", tender_id),
    ]
    job = client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params))
    job.result()


def _diff_fields(old: dict, new: dict) -> list[str]:
    """Which content fields actually changed, for the tender_snapshots log.
    Uses the same content view as compute_content_hash() so bookkeeping
    noise (document_id/parsed_at) never shows up as a false "documents"
    change."""
    old_view, new_view = _content_view(old), _content_view(new)
    return [field for field in CONTENT_FIELDS if old_view[field] != new_view[field]]


def _log_snapshot(client: bigquery.Client, tender_id: str, content_hash: str,
                   changed_fields: list[str], full_record: dict) -> None:
    row = {
        "snapshot_id": str(uuid.uuid4()),
        "tender_id": tender_id,
        "scanned_at": _now_iso(),
        "content_hash": content_hash,
        "changed_fields": changed_fields,
        "raw_payload": json.dumps(full_record, default=str),
    }
    errors = client.insert_rows_json(SNAPSHOTS_TABLE, [row])
    if errors:
        raise RuntimeError(f"Failed to write tender_snapshots row: {errors}")


def upsert_tender(client: bigquery.Client, record: dict) -> dict:

    record = deepcopy(record)
    record["documents"] = _prepare_documents(record.get("documents"))
    record.setdefault("raw_extra", None)
    new_hash = compute_content_hash(record)

    existing = _find_existing(client, record)
    now = _now_iso()

    if existing is None:
        row = {col: record.get(col) for col in ALL_COLUMNS if col in CONTENT_FIELDS
               or col in ("source_reference_id", "source_id", "source_url")}
        row["tender_id"] = str(uuid.uuid4())
        row["content_hash"] = new_hash
        row["first_seen_at"] = now
        row["last_scanned_at"] = now
        row["updated_at"] = now
        row["raw_extra"] = record.get("raw_extra")
        _load_row(client, row)
        return {"action": "inserted", "tender_id": row["tender_id"]}

    if existing["content_hash"] == new_hash:
        _bump_last_scanned(client, existing["tender_id"])
        return {"action": "noop", "tender_id": existing["tender_id"]}

    changed_fields = _diff_fields(existing, record)
    row = {col: record.get(col) for col in ALL_COLUMNS if col in CONTENT_FIELDS
           or col in ("source_reference_id", "source_id", "source_url")}
    row["tender_id"] = existing["tender_id"]
    row["content_hash"] = new_hash
    row["first_seen_at"] = existing["first_seen_at"]
    row["last_scanned_at"] = now
    row["updated_at"] = now
    row["raw_extra"] = record.get("raw_extra")

    _delete_row(client, existing["tender_id"])
    _load_row(client, row)
    _log_snapshot(client, existing["tender_id"], new_hash, changed_fields, record)
    return {"action": "updated", "tender_id": existing["tender_id"], "changed_fields": changed_fields}
