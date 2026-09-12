from functools import lru_cache

from google.cloud import bigquery

from app.config import settings

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


@lru_cache
def get_client() -> bigquery.Client:
    return bigquery.Client(project=settings.google_cloud_project)


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
    select_cols = ", ".join(ALL_COLUMNS)
    conditions: list[str] = []
    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
        bigquery.ScalarQueryParameter("offset", "INT64", offset),
    ]

    if status:
        conditions.append("status = @status")
        params.append(bigquery.ScalarQueryParameter("status", "STRING", status))
    if category:
        conditions.append("category = @category")
        params.append(bigquery.ScalarQueryParameter("category", "STRING", category))
    if source_id:
        conditions.append("source_id = @source_id")
        params.append(bigquery.ScalarQueryParameter("source_id", "STRING", source_id))
        
    if location:
        conditions.append("LOWER(location) LIKE @location")
        params.append(bigquery.ScalarQueryParameter("location", "STRING", f"%{location.lower()}%"))
        
    if min_value is not None:
        conditions.append("value_amount >= @min_value")
        params.append(bigquery.ScalarQueryParameter("min_value", "FLOAT64", min_value))
    if max_value is not None:
        conditions.append("value_amount <= @max_value")
        params.append(bigquery.ScalarQueryParameter("max_value", "FLOAT64", max_value))
        
    if closing_before:
        conditions.append("closing_date <= @closing_before")
        params.append(bigquery.ScalarQueryParameter("closing_before", "DATE", closing_before))
    if closing_after:
        conditions.append("closing_date >= @closing_after")
        params.append(bigquery.ScalarQueryParameter("closing_after", "DATE", closing_after))
        
    if year:
        conditions.append("(CAST(closing_date AS STRING) LIKE @year OR CAST(publish_date AS STRING) LIKE @year)")
        params.append(bigquery.ScalarQueryParameter("year", "STRING", f"{year}%"))

    if q:
        conditions.append("(LOWER(title) LIKE @q OR LOWER(description) LIKE @q)")
        params.append(bigquery.ScalarQueryParameter("q", "STRING", f"%{q.lower()}%"))

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

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
        if record.get("documents"):
            record["documents"] = [dict(d) for d in record["documents"]]
        rows.append(record)
    return rows