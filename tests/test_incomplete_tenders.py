"""
A tender stored with something missing that another try could fix (a document
that would not download, say) is marked in its raw_extra, and the next night's
resume fetches it again instead of skipping it as "already stored".
"""

import json
import types

import pytest

from error_scrapers import run_notes

try:  # manager needs the google libraries; skip those tests where they are missing
    import ingestion.bigquery_client as bigquery_client
    _real_get_client = bigquery_client.get_client
    bigquery_client.get_client = lambda: object()
    try:
        import manager
    finally:
        bigquery_client.get_client = _real_get_client
except ImportError:  # pragma: no cover
    manager = None

needs_manager = pytest.mark.skipif(manager is None, reason="google libraries not installed")


@pytest.fixture(autouse=True)
def _clean():
    run_notes.reset()
    yield
    run_notes.reset()


def _partial(ref, *reasons):
    for category, detail in reasons:
        run_notes.note_reason(category, detail)
    run_notes.note_tender(ref, run_notes.TENDER_PARTIAL)


# --- which problems are worth another try -----------------------------------

def test_a_failed_download_is_worth_another_try():
    _partial("PUT-1", (run_notes.DOWNLOAD, "document package"))
    assert run_notes.needs_another_try("PUT-1") is True


def test_a_portal_that_would_not_open_the_documents_is_worth_another_try():
    _partial("VP1", (run_notes.PORTAL, None))
    assert run_notes.needs_another_try("VP1") is True


def test_a_scrape_error_is_worth_another_try():
    run_notes.note_tender_error("T9", ValueError("boom"))
    assert run_notes.needs_another_try("T9") is True


@pytest.mark.parametrize("category", [run_notes.LOCKED, run_notes.UNREADABLE,
                                      run_notes.EMPTY, run_notes.SIZE])
def test_a_problem_that_would_be_the_same_next_time_is_not(category):
    _partial("T1", (category, "file.pdf"))
    assert run_notes.needs_another_try("T1") is False


def test_one_fixable_problem_among_unfixable_ones_is_enough():
    _partial("T1", (run_notes.LOCKED, "a.pdf"), (run_notes.DOWNLOAD, "b.pdf"))
    assert run_notes.needs_another_try("T1") is True


def test_a_clean_tender_and_an_unknown_one_are_not():
    run_notes.note_tender("OK1", run_notes.SITE_SUCCESS)
    assert run_notes.needs_another_try("OK1") is False
    assert run_notes.needs_another_try("never-seen") is False


def test_a_tender_ref_that_is_a_web_address_matches_its_folder_name():
    _partial("https://x.example/tender/view?id=336317", (run_notes.DOWNLOAD, None))
    assert run_notes.needs_another_try("336317") is True


# --- the mark ------------------------------------------------------------------

def test_the_mark_is_added_to_nothing():
    assert json.loads(run_notes.flag_incomplete(None)) == {"scrape_incomplete": True, "scrape_attempts": 1}


def test_the_mark_keeps_what_raw_extra_already_held():
    marked = run_notes.flag_incomplete(json.dumps({"tags": ["a", "b"]}), prior_tries=1)
    assert json.loads(marked) == {"tags": ["a", "b"], "scrape_incomplete": True, "scrape_attempts": 2}


def test_the_mark_copes_with_a_dict_or_junk():
    assert json.loads(run_notes.flag_incomplete({"tags": ["x"]}))["tags"] == ["x"]
    assert json.loads(run_notes.flag_incomplete("not json"))["scrape_attempts"] == 1


# --- what the next night skips -------------------------------------------------

def test_a_flagged_tender_with_tries_left_is_not_skipped():
    skip, tries = run_notes.urls_to_treat_as_stored([
        ("a", None, None), ("b", "true", 1), ("c", "true", None), ("d", "true", 3), ("e", "false", None)])
    assert skip == {"a", "d", "e"}
    assert tries == {"b": 1, "c": 0, "d": 3}


def test_a_flagged_tender_is_skipped_again_once_its_tries_run_out():
    skip, _ = run_notes.urls_to_treat_as_stored([("x", "true", run_notes.MAX_INCOMPLETE_TRIES)])
    assert skip == {"x"}


# --- the manager's use of it ------------------------------------------------------

class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def query(self, sql, job_config=None):
        self.sql = sql
        return types.SimpleNamespace(result=lambda: [
            types.SimpleNamespace(source_url=u, incomplete=f, tries=t) for u, f, t in self.rows])


@needs_manager
def test_the_next_runs_known_urls_leave_out_tenders_stored_incomplete(monkeypatch):
    bq = _Rows([("https://a", None, None), ("https://b", "true", 1), ("https://c", "true", 3)])
    monkeypatch.setattr(manager, "bq_client", bq)
    monkeypatch.setattr(manager, "_INCOMPLETE_TRIES", {})
    assert manager._known_source_urls("vic_buyingfor") == {"https://a", "https://c"}
    assert manager._INCOMPLETE_TRIES == {"https://b": 1, "https://c": 3}
    assert "scrape_incomplete" in bq.sql


@needs_manager
def test_a_failed_lookup_still_treats_nothing_as_stored(monkeypatch):
    class Broken:
        def query(self, *a, **k):
            raise RuntimeError("bigquery down")
    monkeypatch.setattr(manager, "bq_client", Broken())
    assert manager._known_source_urls("vic_buyingfor") == set()


@needs_manager
def test_an_incomplete_tender_is_marked_before_it_is_stored(tmp_path, monkeypatch):
    upserted = []
    monkeypatch.setattr(manager, "upsert_tender", lambda client, record: (upserted.append(record),
                        {"action": "inserted", "tender_id": "id"})[1])
    monkeypatch.setattr(manager.attachment_store, "upload_tender_attachments", lambda *a, **k: [])
    monkeypatch.setattr(manager, "generate_embedding", lambda text: None)
    monkeypatch.setattr(manager, "_INCOMPLETE_TRIES", {"https://x/1": 1})
    (tmp_path / "PUT-1").mkdir()

    process = lambda path: {"title": "T", "description": "D", "raw_extra": json.dumps({"tags": ["z"]})}
    relevance = lambda tender: tender
    _partial("PUT-1", (run_notes.DOWNLOAD, "document package"))

    monkeypatch.setattr(manager.common, "read_source_url", lambda path: "https://x/1")
    manager._process_one_tender(1, 1, "PUT-1", str(tmp_path), {"PUT-1": ("vic_buyingfor", [], "https://x/1")},
                                process, relevance)

    extra = json.loads(upserted[0]["raw_extra"])
    assert extra == {"tags": ["z"], "scrape_incomplete": True, "scrape_attempts": 2}


@needs_manager
def test_a_complete_tender_is_not_marked(tmp_path, monkeypatch):
    upserted = []
    monkeypatch.setattr(manager, "upsert_tender", lambda client, record: (upserted.append(record),
                        {"action": "inserted", "tender_id": "id"})[1])
    monkeypatch.setattr(manager.attachment_store, "upload_tender_attachments", lambda *a, **k: [])
    monkeypatch.setattr(manager, "generate_embedding", lambda text: None)
    (tmp_path / "OK-1").mkdir()
    run_notes.note_tender("OK-1", run_notes.SITE_SUCCESS)

    monkeypatch.setattr(manager.common, "read_source_url", lambda path: "https://x/2")
    manager._process_one_tender(1, 1, "OK-1", str(tmp_path), {"OK-1": ("vic_buyingfor", [], "https://x/2")},
                                lambda path: {"title": "T", "description": "D", "raw_extra": None},
                                lambda tender: tender)

    assert upserted[0]["raw_extra"] is None
