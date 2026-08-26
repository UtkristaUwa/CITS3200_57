from datetime import date, datetime

from pydantic import BaseModel


class DocumentOut(BaseModel):
    document_id: str | None = None
    file_name: str
    file_type: str | None = None
    extracted_text: str | None = None
    parsed_at: datetime | None = None


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


class HealthOut(BaseModel):
    status: str
