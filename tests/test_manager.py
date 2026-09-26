"""
manager._merge_document_records: pairing attachment_store's storage records
with the extracted text tender_processor read from each attachment's sibling
<base>.txt file, into the shape the `documents` BigQuery column needs.
"""

import builtins
import os
import sys
import types

import ingestion.bigquery_client as bigquery_client


# manager creates its BigQuery client at import time. Keep these unit tests
# offline without changing manager's production initialization behaviour.
_real_get_client = bigquery_client.get_client
bigquery_client.get_client = lambda: object()
try:
    import manager
finally:
    bigquery_client.get_client = _real_get_client

from processing.runtime_config import RuntimeConfigPreparation


def test_pairs_an_attachment_with_its_extracted_text():
    attachments = [{"file_name": "Requirements.pdf", "storage_uri": "gs://b/Requirements.pdf"}]
    txt_documents = [{"file_name": "Requirements.txt", "extracted_text": "the extracted body"}]

    merged = manager._merge_document_records(attachments, txt_documents)

    assert merged == [{
        "file_name": "Requirements.pdf",
        "storage_uri": "gs://b/Requirements.pdf",
        "extracted_text": "the extracted body",
    }]


def test_a_genuine_txt_attachment_is_paired_with_its_own_content():
    # A real portal-provided .txt file: tender_processor reads every .txt
    # in the folder as its own "extracted text", including this one.
    attachments = [{"file_name": "Addendum 1.txt", "storage_uri": "gs://b/Addendum 1.txt"}]
    txt_documents = [{"file_name": "Addendum 1.txt", "extracted_text": "a real attachment"}]

    merged = manager._merge_document_records(attachments, txt_documents)

    assert merged[0]["extracted_text"] == "a real attachment"


def test_an_attachment_with_no_extraction_gets_no_text_rather_than_failing():
    attachments = [{"file_name": "scan.pdf", "storage_uri": "gs://b/scan.pdf"}]

    merged = manager._merge_document_records(attachments, [])

    assert merged[0]["extracted_text"] is None


def test_the_tenders_own_page_text_is_dropped_not_carried_into_documents():
    # <REF>.txt has no matching attachment -- it must not end up in
    # `documents` at all, real or otherwise.
    attachments = [{"file_name": "Requirements.pdf", "storage_uri": "gs://b/Requirements.pdf"}]
    txt_documents = [
        {"file_name": "Requirements.txt", "extracted_text": "body"},
        {"file_name": "GO8232.txt", "extracted_text": "page text"},
    ]

    merged = manager._merge_document_records(attachments, txt_documents)

    assert [d["file_name"] for d in merged] == ["Requirements.pdf"]


def test_no_attachments_means_no_documents():
    txt_documents = [{"file_name": "GO8232.txt", "extracted_text": "page text"}]

    assert manager._merge_document_records([], txt_documents) == []


def test_runtime_config_is_prepared_before_tender_processor_import(tmp_path, monkeypatch):
    events = []

    def prepare(runtime_directory):
        events.append(("prepare", runtime_directory))
        return RuntimeConfigPreparation(
            active=True,
            path=str(tmp_path / "runtime_tender_processor.cfg"),
            reason="runtime configuration active",
        )

    fake_processor = types.ModuleType("processing.tender_processor")
    fake_processor.process_tender = object()
    monkeypatch.setitem(sys.modules, "processing.tender_processor", fake_processor)
    monkeypatch.setattr(manager, "prepare_runtime_config", prepare)

    real_import = builtins.__import__

    def tracking_import(name, *args, **kwargs):
        if name == "processing.tender_processor":
            events.append(("import", name))
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)

    loaded = manager._load_process_tender(str(tmp_path))

    assert loaded is fake_processor.process_tender
    assert events == [
        ("prepare", str(tmp_path)),
        ("import", "processing.tender_processor"),
    ]


def test_manager_fallback_import_sees_no_runtime_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDER_PROCESSOR_CONFIG", "/tmp/stale.cfg")

    def prepare(_runtime_directory):
        os.environ.pop("TENDER_PROCESSOR_CONFIG", None)
        return RuntimeConfigPreparation(active=False, path=None, reason="test fallback")

    fake_processor = types.ModuleType("processing.tender_processor")
    fake_processor.process_tender = object()
    monkeypatch.setitem(sys.modules, "processing.tender_processor", fake_processor)
    monkeypatch.setattr(manager, "prepare_runtime_config", prepare)

    real_import = builtins.__import__

    def checking_import(name, *args, **kwargs):
        if name == "processing.tender_processor":
            assert "TENDER_PROCESSOR_CONFIG" not in os.environ
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checking_import)

    assert manager._load_process_tender(str(tmp_path)) is fake_processor.process_tender
