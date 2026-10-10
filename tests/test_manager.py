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
        "file_type": None,
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


def test_a_spreadsheet_is_paired_with_its_full_name_txt():
    # A spreadsheet's text lives in the scraper's <file name>.txt.
    attachments = [{"file_name": "Breakdown.xlsx", "storage_uri": "gs://b/Breakdown.xlsx"}]
    txt_documents = [{"file_name": "Breakdown.xlsx.txt", "extracted_text": "sheet text"}]

    merged = manager._merge_document_records(attachments, txt_documents)

    assert merged[0]["extracted_text"] == "sheet text"


def test_the_fuller_scraper_extraction_wins_over_the_paragraph_only_one():
    # A form made of tables: the old stage finds no body paragraphs and the
    # scraper's extraction (which reads tables) is the only real text.
    attachments = [{"file_name": "Form.docx", "storage_uri": "gs://b/Form.docx"}]
    txt_documents = [
        {"file_name": "Form.txt", "extracted_text": "body only"},
        {"file_name": "Form.docx.txt", "extracted_text": "body plus table cells"},
    ]

    merged = manager._merge_document_records(attachments, txt_documents)

    assert merged[0]["extracted_text"] == "body plus table cells"


def test_the_base_name_txt_is_still_used_when_there_is_no_full_name_txt():
    attachments = [{"file_name": "Old.pdf", "storage_uri": "gs://b/Old.pdf"}]
    txt_documents = [{"file_name": "Old.txt", "extracted_text": "from the old stage"}]

    merged = manager._merge_document_records(attachments, txt_documents)

    assert merged[0]["extracted_text"] == "from the old stage"


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


# ---------------------------------------------------------------------------
# Browser clean-up between portals
# ---------------------------------------------------------------------------

import signal
import threading
import time

import pytest

from error_scrapers import common


def _fake_proc(root, processes):
    for pid, name in processes.items():
        pid_dir = root / str(pid)
        pid_dir.mkdir()
        (pid_dir / "comm").write_text(name + "\n", encoding="utf-8")
    (root / "cpuinfo").write_text("not a process", encoding="utf-8")
    (root / "self").mkdir()


def test_leftover_chrome_processes_are_killed_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")
    _fake_proc(tmp_path, {
        101: "chrome", 102: "chromedriver", 103: "uc_driver",
        104: "python", 105: "chrome_crashpad", 106: "bash",
    })
    killed = []

    count = manager._kill_stray_browsers(str(tmp_path), kill=lambda pid, sig: killed.append((pid, sig)))

    assert count == 4
    assert sorted(pid for pid, _ in killed) == [101, 102, 103, 105]
    assert {sig for _, sig in killed} == {signal.SIGKILL}


def test_the_clean_up_does_nothing_outside_the_container(tmp_path, monkeypatch):
    # On a developer's machine this would close their own Chrome.
    monkeypatch.delenv("RUNNING_IN_CONTAINER", raising=False)
    _fake_proc(tmp_path, {101: "chrome"})
    killed = []

    assert manager._kill_stray_browsers(str(tmp_path), kill=lambda *a: killed.append(a)) == 0
    assert killed == []


def test_a_browser_that_already_exited_does_not_stop_the_clean_up(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")
    _fake_proc(tmp_path, {101: "chrome", 102: "chromedriver"})

    def kill(pid, sig):
        if pid == 101:
            raise ProcessLookupError
        return None

    assert manager._kill_stray_browsers(str(tmp_path), kill=kill) == 1


def test_a_missing_proc_directory_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNING_IN_CONTAINER", "1")
    assert manager._kill_stray_browsers(str(tmp_path / "nope")) == 0


# ---------------------------------------------------------------------------
# One scraper
# ---------------------------------------------------------------------------

def test_scrape_one_attributes_the_new_folders_to_the_scraper(tmp_path):
    (tmp_path / "EXISTING").mkdir()

    def scrape(limit, output_dir):
        os.makedirs(os.path.join(output_dir, "T1"))
        return common.ScrapeResult(common.SITE_SUCCESS, "portal", 1)

    manifest, failure, health, row = manager._scrape_one(
        "portal", scrape, str(tmp_path), 0, False, "2026-01-01T00:00:00+00:00"
    )

    assert manifest == {"T1": ("portal", None, None)}
    assert failure is None
    assert health["website"] == "PORTAL"
    assert health["status"] == "Success"
    assert row[:3] == ("PORTAL", common.SITE_SUCCESS, 1)


def test_scrape_one_reports_a_failure_code(tmp_path):
    def scrape(limit, output_dir):
        return common.ScrapeResult(common.SITE_BOT_BLOCKED, "portal", 0)

    manifest, failure, health, _row = manager._scrape_one(
        "portal", scrape, str(tmp_path), 0, False, "now"
    )

    assert manifest == {}
    assert failure[0] == "portal"
    assert health["status_color"] == "error"


def test_scrape_one_turns_an_exception_into_a_failure(tmp_path):
    def scrape(limit, output_dir):
        raise RuntimeError("boom")

    manifest, failure, health, row = manager._scrape_one(
        "portal", scrape, str(tmp_path), 0, False, "now"
    )

    assert manifest == {}
    assert failure == ("portal", "exception: boom")
    assert health["message"] == "Unhandled exception: boom"
    assert row[1] is None


# ---------------------------------------------------------------------------
# The cloud pipeline runs one portal at a time
# ---------------------------------------------------------------------------

class _Exited(Exception):
    def __init__(self, code):
        self.code = code


@pytest.fixture
def pipeline(monkeypatch):
    """manager.main() with every outside system replaced by a recorder."""
    state = {"processed": [], "published": [], "cleanups": 0, "seen_by_scraper": {},
             "delay": 0, "active": {"now": 0, "max": 0}}
    lock = threading.Lock()

    monkeypatch.setattr(sys, "argv", ["manager.py"])
    # One tender at a time unless a test asks for more, so order is predictable.
    monkeypatch.setenv("PIPELINE_WORKERS", "1")
    monkeypatch.setattr(manager, "_load_process_tender", lambda d: fake_process)
    monkeypatch.setattr(manager, "_load_determine_relevance", lambda: (lambda tender: tender))
    monkeypatch.setattr(manager.attachment_store, "upload_tender_attachments",
                        lambda path, **kw: [])
    monkeypatch.setattr(manager, "upsert_tender",
                        lambda client, tender: {"action": "inserted", "tender_id": "id"})
    monkeypatch.setattr(manager, "generate_embedding", lambda text: [])
    monkeypatch.setattr(manager, "publish_health_status_to_gcs",
                        lambda records, bucket_name: state["published"].append(len(records)))

    def count_cleanup():
        state["cleanups"] += 1
        return 0
    monkeypatch.setattr(manager, "_kill_stray_browsers", count_cleanup)

    def fake_process(tender_path):
        with lock:
            state["active"]["now"] += 1
            state["active"]["max"] = max(state["active"]["max"], state["active"]["now"])
        time.sleep(state["delay"])
        with lock:
            state["active"]["now"] -= 1
            state["processed"].append(os.path.basename(tender_path))
        return {"title": "t", "description": "d", "documents": []}

    def fake_exit(code):
        raise _Exited(code)
    monkeypatch.setattr(os, "_exit", fake_exit)

    def make_scraper(name, folders, code=common.SITE_SUCCESS):
        def scrape(limit, output_dir):
            state["seen_by_scraper"][name] = sorted(os.listdir(output_dir))
            for folder in folders:
                os.makedirs(os.path.join(output_dir, folder))
                with open(os.path.join(output_dir, folder, f"{folder}.txt"), "w") as f:
                    f.write("page text")
            return common.ScrapeResult(code, name, len(folders))
        return scrape

    state["make_scraper"] = make_scraper
    return state


def _run_main():
    with pytest.raises(_Exited) as exited:
        manager.main()
    return exited.value.code


def test_each_portal_is_processed_and_cleared_before_the_next_starts(pipeline, monkeypatch):
    make = pipeline["make_scraper"]
    monkeypatch.setattr(manager, "SCRAPERS", [
        ("first", make("first", ["A1", "A2"])),
        ("second", make("second", ["B1"])),
    ])

    assert _run_main() == 0

    assert pipeline["processed"] == ["A1", "A2", "B1"]
    # The second scraper started with none of the first portal's folders left.
    assert "A1" not in pipeline["seen_by_scraper"]["second"]
    assert "A2" not in pipeline["seen_by_scraper"]["second"]
    assert pipeline["cleanups"] == 2  # browsers cleaned up after every portal
    # The health table is refreshed after every portal, cumulatively.
    assert pipeline["published"] == [1, 2]


def test_a_failing_portal_does_not_stop_the_others_but_the_run_exits_1(pipeline, monkeypatch):
    make = pipeline["make_scraper"]
    monkeypatch.setattr(manager, "SCRAPERS", [
        ("blocked", make("blocked", [], code=common.SITE_BOT_BLOCKED)),
        ("good", make("good", ["G1"])),
    ])

    assert _run_main() == 1
    assert pipeline["processed"] == ["G1"]


def test_a_portal_with_no_tenders_is_skipped_not_fatal(pipeline, monkeypatch):
    make = pipeline["make_scraper"]
    monkeypatch.setattr(manager, "SCRAPERS", [
        ("empty", make("empty", [])),
        ("good", make("good", ["G1"])),
    ])

    assert _run_main() == 0
    assert pipeline["processed"] == ["G1"]


def test_a_run_that_scrapes_nothing_exits_1(pipeline, monkeypatch):
    make = pipeline["make_scraper"]
    monkeypatch.setattr(manager, "SCRAPERS", [("empty", make("empty", []))])

    with pytest.raises(SystemExit) as exited:
        manager.main()
    assert exited.value.code == 1


# ---------------------------------------------------------------------------
# Stage 3 works on several tenders at once
# ---------------------------------------------------------------------------

def test_tenders_are_processed_several_at_a_time(pipeline, monkeypatch):
    monkeypatch.setenv("PIPELINE_WORKERS", "4")
    pipeline["delay"] = 0.05
    folders = [f"T{i}" for i in range(8)]
    monkeypatch.setattr(manager, "SCRAPERS", [
        ("portal", pipeline["make_scraper"]("portal", folders)),
    ])

    assert _run_main() == 0

    assert sorted(pipeline["processed"]) == sorted(folders)  # every tender, exactly once
    assert 2 <= pipeline["active"]["max"] <= 4               # overlapping, never above the cap


def test_one_worker_means_one_tender_at_a_time_in_order(pipeline, monkeypatch):
    pipeline["delay"] = 0.01
    folders = [f"T{i}" for i in range(5)]
    monkeypatch.setattr(manager, "SCRAPERS", [
        ("portal", pipeline["make_scraper"]("portal", folders)),
    ])

    assert _run_main() == 0

    assert pipeline["processed"] == folders
    assert pipeline["active"]["max"] == 1


def test_a_tender_that_blows_up_does_not_stop_the_others_but_fails_the_run(pipeline, monkeypatch):
    monkeypatch.setenv("PIPELINE_WORKERS", "4")

    def upload(path, **kwargs):
        if os.path.basename(path) == "T2":
            raise RuntimeError("storage exploded")
        return []
    monkeypatch.setattr(manager.attachment_store, "upload_tender_attachments", upload)
    folders = [f"T{i}" for i in range(5)]
    monkeypatch.setattr(manager, "SCRAPERS", [
        ("portal", pipeline["make_scraper"]("portal", folders)),
    ])

    assert _run_main() == 1
    assert sorted(pipeline["processed"]) == ["T0", "T1", "T3", "T4"]


@pytest.mark.parametrize("value, expected", [
    (None, 4), ("8", 8), ("1", 1), ("0", 1), ("-3", 1), ("lots", 4), ("", 4),
])
def test_the_worker_count_setting_is_forgiving(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("PIPELINE_WORKERS", raising=False)
    else:
        monkeypatch.setenv("PIPELINE_WORKERS", value)
    assert manager._worker_count() == expected


def test_freeing_memory_is_harmless_where_glibc_is_missing(monkeypatch):
    import ctypes

    def no_libc(name):
        raise OSError("no libc.so.6 here")
    monkeypatch.setattr(ctypes, "CDLL", no_libc)

    manager._free_memory()  # must not raise
