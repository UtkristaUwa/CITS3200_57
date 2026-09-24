import json
import re
from datetime import date, datetime

from pydantic import BaseModel, field_validator, model_validator

# Old-style pipeline metadata files were stored as "<tender_id>.txt" with no
# storage_uri. New-style use the __tender__ prefix. Both should be hidden.
_PIPELINE_ID_RE = re.compile(r'^[A-Za-z0-9._-]+\.txt$')


class DocumentOut(BaseModel):
    document_id: str | None = None
    file_name: str
    file_type: str | None = None
    extracted_text: str | None = None
    parsed_at: datetime | None = None
    storage_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_storage_url(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if data.get("storage_url"):
            return data
        uri = data.get("storage_uri")
        if uri and isinstance(uri, str) and uri.startswith("gs://"):
            data = dict(data)
            data["storage_url"] = "https://storage.googleapis.com/" + uri[len("gs://"):]
        return data


class TenderOut(BaseModel):
    tender_id: str
    source_reference_id: str | None = None
    source_id: str | None = None
    source_url: str | None = None

    title: str
    issuing_agency: str | None = None
    category: str | None = None
    status: str | None = None

    publish_date: date | None = None
    closing_date: date | None = None

    value_amount: float | None = None
    value_currency: str | None = None
    value_notes: str | None = None

    location: str | None = None
    description: str | None = None
    summary_headline: str | None = None

    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    lodgment_address: str | None = None

    documents: list[DocumentOut] = []

    content_hash: str | None = None
    first_seen_at: datetime
    last_scanned_at: datetime
    updated_at: datetime

    raw_extra: dict | None = None

    @field_validator("title", mode="before")
    @classmethod
    def default_title(cls, v):
        return v or "(Untitled tender)"

    @field_validator("raw_extra", mode="before")
    @classmethod
    def parse_raw_extra(cls, v):
        # BigQuery's JSON-typed columns come back as raw JSON strings (not
        # parsed dicts) from job.result()'s REST row iterator.
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("documents", mode="before")
    @classmethod
    def strip_pipeline_files(cls, docs):
        # Safety net: drop __tender__*.txt rows that the pipeline writes for
        # its own use and that should never surface as downloadable attachments.
        if not isinstance(docs, list):
            return docs
        return [
            d for d in docs
            if not (isinstance(d, dict) and (
                d.get("file_name", "").startswith("__tender__") or
                (not d.get("storage_uri") and _PIPELINE_ID_RE.match(d.get("file_name", "")))
            ))
        ]


class HealthOut(BaseModel):
    status: str
