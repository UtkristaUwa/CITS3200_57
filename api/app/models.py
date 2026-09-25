import json
from datetime import date, datetime

from pydantic import BaseModel, field_validator, model_validator


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
    relevance_score: int | None = None

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
    def keep_only_uploaded_files(cls, docs):
        # Only surface documents that have been uploaded to GCS. Everything
        # without a storage_uri is either a pipeline metadata file (the
        # tender's own scraped page text) or an unprocessed text extraction
        # — neither is a user-facing attachment. A real attachment that
        # happens to be .txt (a portal-provided text file the scraper's
        # manifest reported) still gets a storage_uri from attachment_store
        # and is meant to surface — see
        # tests/test_attachment_store.py::test_manifest_is_believed_over_guessing_from_the_folder.
        if not isinstance(docs, list):
            return docs
        return [d for d in docs if isinstance(d, dict) and d.get("storage_uri")]


class HealthOut(BaseModel):
    status: str
