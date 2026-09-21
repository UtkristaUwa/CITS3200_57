import json
import logging
from datetime import date
from functools import lru_cache

from google import genai
from google.cloud import bigquery

from app.config import settings

logger = logging.getLogger("TenderSearch")

# Mirrors ALL_COLUMNS in ingestion/bigquery_client.py — kept in sync manually
# since api/ and ingestion/ are separate deployable units, not a shared package.
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

LOCATION = "australia-southeast1"


@lru_cache
def get_client() -> bigquery.Client:
    return bigquery.Client(project=settings.google_cloud_project, location=LOCATION)


@lru_cache
def get_ai_client() -> genai.Client:
    return genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location=LOCATION,
    )


def generate_query_embedding(query_text: str) -> list[float]:
    """Generates a 768-dim float vector for semantic search."""
    cleaned = (query_text or "").strip()
    if not cleaned:
        raise ValueError("Cannot generate embedding for an empty query.")

    ai_client = get_ai_client()
    try:
        response = ai_client.models.embed_content(
            model="text-embedding-004",
            contents=cleaned,
        )
        vector = response.embeddings[0].values

        # -------------------------------------------------------------
        # Fix 1: Validate dimensions before handing off to BigQuery
        # -------------------------------------------------------------
        if not vector or len(vector) != 768:
            raise ValueError(f"Expected 768-dim vector, got {len(vector) if vector else 0}")

        return vector

    except Exception as exc:
        logger.error("🔍 Vertex AI embedding generation failed: %s", exc)
        raise


def list_tenders(
    client: bigquery.Client,
    limit: int,
    offset: int,
    status: str | None = None,
    category: str | None = None,
    source_id: str | None = None,
    location: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    closing_before: date | None = None,
    closing_after: date | None = None,
    year: str | None = None,
    q: str | None = None,
) -> list[dict]:
    """
    SELECT from `tenders` with optional filters. All filter values are bound
    as query parameters — the WHERE clause only ever inserts hardcoded
    column names/operators, never a value, so this stays injection-safe.
    """
    conditions: list[str] = []
    params: list[bigquery.ScalarQueryParameter | bigquery.ArrayQueryParameter] = [
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
        bigquery.ScalarQueryParameter("offset", "INT64", offset),
    ]

    clean_q = (q or "").strip()
    col_prefix = "base." if clean_q else ""

    if status:
        conditions.append(f"{col_prefix}status = @status")
        params.append(bigquery.ScalarQueryParameter("status", "STRING", status))
    if category:
        conditions.append(f"{col_prefix}category = @category")
        params.append(bigquery.ScalarQueryParameter("category", "STRING", category))
    if source_id:
        conditions.append(f"{col_prefix}source_id = @source_id")
        params.append(bigquery.ScalarQueryParameter("source_id", "STRING", source_id))

    if location:
        conditions.append(f"LOWER({col_prefix}location) LIKE @location")
        params.append(bigquery.ScalarQueryParameter("location", "STRING", f"%{location.lower()}%"))

    if min_value is not None:
        conditions.append(f"{col_prefix}value_amount >= @min_value")
        params.append(bigquery.ScalarQueryParameter("min_value", "FLOAT64", min_value))
    if max_value is not None:
        conditions.append(f"{col_prefix}value_amount <= @max_value")
        params.append(bigquery.ScalarQueryParameter("max_value", "FLOAT64", max_value))

    if closing_before:
        conditions.append(f"{col_prefix}closing_date <= @closing_before")
        params.append(bigquery.ScalarQueryParameter("closing_before", "DATE", closing_before))
    if closing_after:
        conditions.append(f"{col_prefix}closing_date >= @closing_after")
        params.append(bigquery.ScalarQueryParameter("closing_after", "DATE", closing_after))

    if year:
        year_int = int(year)
        conditions.append(
            #f"(EXTRACT(YEAR FROM {col_prefix}closing_date) = @year OR EXTRACT(YEAR FROM {col_prefix}publish_date) = @year)"
            f"(({col_prefix}closing_date IS NOT NULL AND EXTRACT(YEAR FROM {col_prefix}closing_date) = @year) "
            f"OR ({col_prefix}publish_date IS NOT NULL AND EXTRACT(YEAR FROM {col_prefix}publish_date) = @year))"
        )
        params.append(bigquery.ScalarQueryParameter("year", "INT64", year_int))

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    if clean_q:
        # Case A: Semantic Vector Search
        query_vector = generate_query_embedding(clean_q)
        params.append(bigquery.ArrayQueryParameter("query_vector", "FLOAT64", query_vector))

        select_cols = ", ".join([f"base.{col}" for col in ALL_COLUMNS])

        # top_k should be comfortably large so post-filtering doesn't eliminate all rows
        top_k = max((limit + offset) * 5, 150)
        params.append(bigquery.ScalarQueryParameter("top_k", "INT64", top_k))

        query = f"""
            SELECT {select_cols}, distance
            FROM VECTOR_SEARCH(
                TABLE `{settings.tenders_table}`,
                'embedding',
                (SELECT @query_vector AS embedding),
                top_k => @top_k,
                distance_type => 'COSINE',
                options => '{{"use_brute_force": true}}'
            )
            {where_clause}
            ORDER BY distance ASC
            LIMIT @limit OFFSET @offset
        """
    else:
        # Case B: Standard chronological browse
        select_cols = ", ".join(ALL_COLUMNS)
        query = f"""
            SELECT {select_cols}
            FROM `{settings.tenders_table}`
            {where_clause}
            ORDER BY first_seen_at DESC
            LIMIT @limit OFFSET @offset
        """

    job = client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params))
    rows = []
    for row in job.result():
        record = dict(row)
        record.setdefault("distance", None)  # Ensures consistent dictionary shape
        if record.get("documents"):
            record["documents"] = [dict(d) for d in record["documents"]]
        if isinstance(record.get("raw_extra"), str):
            try:
                record["raw_extra"] = json.loads(record["raw_extra"])
            except (json.JSONDecodeError, TypeError):
                record["raw_extra"] = None
        rows.append(record)
    return rows


def get_locations(client: bigquery.Client) -> list[str]:
    """
    Fetch a deduplicated list of all available locations for the frontend filter dropdown.
    """
    query = f"""
        SELECT DISTINCT location
        FROM `{settings.tenders_table}`
        WHERE location IS NOT NULL AND TRIM(location) != ''
        ORDER BY location
    """
    job = client.query(query)
    return [row["location"] for row in job.result()]