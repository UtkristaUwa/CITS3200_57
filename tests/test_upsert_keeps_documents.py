"""
A scrape with no documents must not wipe a stored tender's documents.

Seen on VP527544: one night's VendorPanel lookup failed, the scraper returned
only the page-text stub, and the upsert replaced a row holding 5 documents with
one holding 0. upsert_tender now keeps the stored row in that case.

No network: BigQuery access is replaced with in-memory fakes.
"""

import pytest

from ingestion import bigquery_client as bq

STORED_DOCS = [
    {"document_id": "d1", "file_name": "a.pdf", "file_type": "pdf",
     "extracted_text": "stored text", "parsed_at": "2026-10-10T00:00:00Z"},
]


def _record(documents, title="EOI Registration"):
    return {
        "source_id": "qld_qtenders",
        "source_reference_id": "VP527544",
        "source_url": "https://example.test/VP527544",
        "title": title,
        "documents": documents,
    }


@pytest.fixture
def calls(monkeypatch):
    seen = {"bump": [], "delete": [], "load": [], "snapshot": []}
    stored = {}

    monkeypatch.setattr(bq, "_find_existing", lambda client, record: stored.get("row"))
    monkeypatch.setattr(bq, "_bump_last_scanned", lambda c, tid: seen["bump"].append(tid))
    monkeypatch.setattr(bq, "_delete_row", lambda c, tid: seen["delete"].append(tid))
    monkeypatch.setattr(bq, "_load_row", lambda c, row: seen["load"].append(row))
    monkeypatch.setattr(bq, "_log_snapshot", lambda *a, **k: seen["snapshot"].append(a))
    seen["stored"] = stored
    return seen


def _store(calls, documents, title="EOI Registration"):
    record = bq._with_defaults(_record(documents, title))
    record["documents"] = bq._prepare_documents(record["documents"])
    record["location_states"] = []
    row = {col: record.get(col) for col in bq.ALL_COLUMNS}
    row["tender_id"] = "tid-1"
    row["content_hash"] = bq.compute_content_hash(record)
    row["first_seen_at"] = "2026-10-01T00:00:00Z"
    calls["stored"]["row"] = row


def test_empty_scrape_keeps_stored_documents(calls):
    _store(calls, STORED_DOCS)

    result = bq.upsert_tender(None, _record([], title="QTENDERS DETAILS: EOI Registration"))

    assert result == {"action": "kept_existing_documents", "tender_id": "tid-1"}
    assert calls["bump"] == ["tid-1"]
    assert calls["delete"] == [] and calls["load"] == [] and calls["snapshot"] == []


def test_scrape_with_documents_still_updates(calls):
    _store(calls, STORED_DOCS)
    changed = [dict(STORED_DOCS[0], extracted_text="new text")]

    result = bq.upsert_tender(None, _record(changed))

    assert result["action"] == "updated"
    assert calls["delete"] == ["tid-1"] and len(calls["load"]) == 1


def test_empty_scrape_of_a_tender_with_no_stored_documents_still_updates(calls):
    _store(calls, [])

    result = bq.upsert_tender(None, _record([], title="New title"))

    assert result["action"] == "updated"
    assert calls["delete"] == ["tid-1"]


def test_new_tender_without_documents_is_still_inserted(calls):
    result = bq.upsert_tender(None, _record([]))

    assert result["action"] == "inserted"
    assert len(calls["load"]) == 1
