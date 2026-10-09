import json
import math
import unicodedata
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_MODEL_ID_LENGTH = 200


def _validate_model_id(value: object) -> object:
    if value is None:
        return value
    if not isinstance(value, str):
        return value
    unsafe_categories = {"Cc", "Cf", "Cs", "Zl", "Zp"}
    if any(unicodedata.category(character) in unsafe_categories for character in value):
        raise ValueError("model identifiers must not contain control or line-separator characters")
    value = value.strip()
    if not value:
        raise ValueError("model identifiers must not be empty")
    return value


class ModelConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    triage_model: str
    extraction_model: str
    generation: str


class ModelConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    triage_model: str | None = Field(default=None, max_length=MAX_MODEL_ID_LENGTH)
    extraction_model: str | None = Field(default=None, max_length=MAX_MODEL_ID_LENGTH)
    generation: str = Field(min_length=1, pattern=r"^[0-9]+$")

    @field_validator("triage_model", "extraction_model", mode="before")
    @classmethod
    def validate_model_id(cls, value: object) -> object:
        return _validate_model_id(value)

    @model_validator(mode="after")
    def require_model_update(self):
        if self.triage_model is None and self.extraction_model is None:
            raise ValueError("at least one model field must be supplied")
        return self


RELEVANCE_PROMPT_FIELDS = (
    "classification_thoughts",
    "focus_areas",
    "work_types",
    "out_of_scope",
)
RELEVANCE_UPDATE_FIELDS = RELEVANCE_PROMPT_FIELDS + (
    "focus_area_weight",
    "work_type_weight",
    "recency_weight",
    "recency_horizon_days",
    "out_of_scope_fit_cap",
    "max_focus_areas",
    "max_work_types",
    "relevance_model",
    "relevance_temperature",
)


def _validate_relevance_text(value: object) -> object:
    if value is None or not isinstance(value, str):
        return value
    unsafe_categories = {"Cf", "Cs", "Zl", "Zp"}
    if any(
        unicodedata.category(character) in unsafe_categories
        or (unicodedata.category(character) == "Cc" and character not in "\r\n\t")
        for character in value
    ):
        raise ValueError("relevance text contains unsupported control characters")
    if any(line.lstrip().startswith(("#", ";")) for line in value.splitlines()):
        raise ValueError("relevance text lines must not start with CFG comment markers")
    value = value.strip()
    if not value:
        raise ValueError("relevance text fields must not be empty")
    return value


class ExtractionPromptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_extraction: str
    generation: str


class ExtractionPromptUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_extraction: str
    generation: str = Field(min_length=1, pattern=r"^[0-9]+$")

    @field_validator("field_extraction", mode="before")
    @classmethod
    def validate_field_extraction(cls, value: object) -> object:
        return _validate_relevance_text(value)


class RelevanceConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification_thoughts: str
    focus_areas: str
    work_types: str
    out_of_scope: str
    focus_area_weight: float
    work_type_weight: float
    recency_weight: float
    recency_horizon_days: int
    out_of_scope_fit_cap: int
    max_focus_areas: int
    max_work_types: int
    relevance_model: str
    relevance_temperature: float
    generation: str


class RelevanceConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification_thoughts: str | None = None
    focus_areas: str | None = None
    work_types: str | None = None
    out_of_scope: str | None = None
    focus_area_weight: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    work_type_weight: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    recency_weight: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    recency_horizon_days: int | None = Field(default=None, gt=0)
    out_of_scope_fit_cap: int | None = Field(default=None, ge=0, le=100)
    max_focus_areas: int | None = Field(default=None, gt=0)
    max_work_types: int | None = Field(default=None, gt=0)
    relevance_model: str | None = Field(default=None, max_length=MAX_MODEL_ID_LENGTH)
    relevance_temperature: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    generation: str = Field(min_length=1, pattern=r"^[0-9]+$")

    @field_validator(*RELEVANCE_PROMPT_FIELDS, mode="before")
    @classmethod
    def validate_relevance_text(cls, value: object) -> object:
        return _validate_relevance_text(value)

    @field_validator("relevance_model", mode="before")
    @classmethod
    def validate_relevance_model(cls, value: object) -> object:
        return _validate_model_id(value)

    @model_validator(mode="after")
    def validate_relevance_update(self):
        if all(getattr(self, field) is None for field in RELEVANCE_UPDATE_FIELDS):
            raise ValueError("at least one relevance field must be supplied")
        if self.focus_area_weight is not None and self.work_type_weight is not None:
            if not math.isclose(
                self.focus_area_weight + self.work_type_weight,
                1.0,
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                raise ValueError("focus area and work type weights must sum to 1")
        return self


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
    # Vector search score (cosine distance computed by BigQuery VECTOR_SEARCH)
    distance: float | None = None

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


class ScraperHealthRecord(BaseModel):
    website: str
    url: str = ""
    last_run: str = ""
    status: str = "Unknown"
    status_color: str = "default"
    message: str = ""

