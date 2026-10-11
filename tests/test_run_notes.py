"""
What the System Health table says for each scraper.

Model: Success (clean, or buy.nsw / VIC stopping on purpose), Partial (any
incomplete tender, with a count and the reasons), Error (the run itself failed).
No network.
"""

import pytest

from error_scrapers import common, reporting, run_notes

OK, PARTIAL, LOGIN = common.SITE_SUCCESS, common.TENDER_PARTIAL, common.SITE_LOGIN_FAILED


@pytest.fixture(autouse=True)
def fresh():
    run_notes.reset()
    yield
    run_notes.reset()


def describe(code, count):
    return run_notes.describe(code, count, run_notes.snapshot(),
                              "explanation", "Label", "colour")


def test_a_clean_run_is_a_success_with_the_count():
    assert describe(OK, 24) == ("Success", "success", "24 tenders scraped.")


def test_one_tender_reads_singular():
    assert describe(OK, 1)[2] == "1 tender scraped."


def test_a_run_with_nothing_new_is_still_a_success():
    assert describe(OK, 0) == ("Success", "success", "No new tenders to scrape.")


def test_a_run_that_set_out_to_do_more_says_so():
    run_notes.note_total(30)
    assert describe(OK, 12)[2] == "12 of 30 tenders scraped."


def test_an_error_keeps_the_old_wording():
    assert describe(LOGIN, 0) == ("Label", "colour", "explanation")


def test_no_code_keeps_the_old_wording():
    assert describe(None, 0) == ("Label", "colour", "explanation")


def test_one_partial_tender_is_counted_and_explained():
    run_notes.note_reason(run_notes.LOCKED, "Price.xls")
    run_notes.note_tender("T25-1527", PARTIAL)
    for ref in ("A", "B", "C"):
        run_notes.note_tender(ref, OK)
    label, colour, message = describe(PARTIAL, 4)
    assert (label, colour) == ("Partial", "warning")
    assert message == ("1 of 4 tenders incomplete: "
                       "T25-1527: password-protected file dropped (Price.xls).")


def test_many_partials_are_tallied_by_reason():
    for i in range(5):
        run_notes.note_reason(run_notes.PORTAL)
        run_notes.note_tender(f"VP{i}", PARTIAL)
    run_notes.note_reason(run_notes.DOWNLOAD, "a.pdf")
    run_notes.note_tender("VP9", PARTIAL)
    message = describe(PARTIAL, 20)[2]
    assert message.startswith("6 of 20 tenders incomplete: ")
    assert "5 x documents could not be opened on the portal" in message
    assert "1 x a document failed to download" in message


def test_an_intended_stop_with_an_incomplete_tender_still_shows_the_count():
    run_notes.note_total(30)
    run_notes.note_reason(run_notes.DOWNLOAD)
    run_notes.note_tender("B1", PARTIAL)
    label, _, message = describe(PARTIAL, 12)
    assert label == "Partial" and message.startswith("1 of 12 tenders incomplete")


def test_partial_with_no_recorded_reason_says_so():
    label, _, message = describe(PARTIAL, 5)
    assert label == "Partial" and "reason not recorded" in message


def test_a_tender_that_raised_is_partial_with_the_error_type():
    reporting.tender_failed(_NullLog(), 1, 3, "https://x/t1", ValueError("boom"))
    message = describe(PARTIAL, 3)[2]
    assert "error while scraping the tender (ValueError)" in message


def test_a_success_on_the_retry_clears_the_earlier_partial():
    run_notes.note_reason(run_notes.PORTAL)
    run_notes.note_tender("VP1", PARTIAL)
    run_notes.note_tender("VP1", OK)  # the QLD second pass followed it
    assert describe(OK, 1)[0] == "Success"


def test_a_problem_found_while_storing_makes_a_success_partial():
    run_notes.note_tender("EAN013", OK)
    run_notes.note_late("EAN013", run_notes.SIZE, "big.zip")
    label, _, message = describe(OK, 1)
    assert label == "Partial"
    assert "EAN013: file over the size limit not stored (big.zip)" in message


def test_a_late_note_adds_to_the_scrape_reason():
    run_notes.note_reason(run_notes.LOCKED, "a.xls")
    run_notes.note_tender("T1", PARTIAL)
    run_notes.note_late("T1", run_notes.SIZE, "b.zip")
    message = describe(PARTIAL, 1)[2]
    assert "password-protected file dropped (a.xls)" in message
    assert "file over the size limit not stored (b.zip)" in message


def test_reset_starts_each_scraper_clean():
    run_notes.note_reason(run_notes.PORTAL)
    run_notes.note_tender("VP1", PARTIAL)
    run_notes.reset()
    assert describe(OK, 2)[0] == "Success"


def test_tender_line_records_the_code():
    run_notes.note_reason(run_notes.EMPTY, "x.pdf")
    reporting.tender_line(_NullLog(), 1, 1, "T9", PARTIAL)
    assert "T9" in run_notes.snapshot().partials


def test_late_note_matches_the_folder_name():
    run_notes.note_tender("26.0000/137057", PARTIAL)
    run_notes.note_late("26.0000_137057", run_notes.SIZE, "big.zip")
    assert list(run_notes.snapshot().partials) == ["26.0000_137057"]


class _NullLog:
    def warning(self, *a, **k):
        pass

    error = info = warning


# --- which tender is it? ------------------------------------------------------

def test_a_tender_that_raised_after_its_id_was_known_is_filed_under_the_id(tmp_path):
    common.tender_dir("GO8232", str(tmp_path))  # the scraper has just read the ID
    reporting.tender_failed(_NullLog(), 1, 3, "https://www.grants.gov.au/Go/Show?GoUuid=abc-123",
                            ValueError("boom"))
    message = describe(PARTIAL, 3)[2]
    assert "GO8232: error while scraping the tender (ValueError)" in message
    assert "http" not in message and "abc-123" not in message


def test_a_tender_that_raised_before_its_id_was_known_is_named_by_the_end_of_its_address():
    reporting.tender_failed(_NullLog(), 1, 3, "https://www.grants.gov.au/Go/Show?GoUuid=abc-123",
                            ValueError("boom"))
    reporting.tender_failed(_NullLog(), 2, 3, "https://tendersonline.nt.gov.au/Tender/Details/T25-1527",
                            ValueError("boom"))
    message = describe(PARTIAL, 3)[2]
    assert "abc-123: error while scraping" in message
    assert "T25-1527: error while scraping" in message
    assert "http" not in message


def test_the_id_of_one_tender_is_not_used_for_the_next(tmp_path):
    common.tender_dir("GO1", str(tmp_path))
    run_notes.note_tender("GO1", OK)  # finished fine
    reporting.tender_failed(_NullLog(), 2, 3, "https://x.gov.au/Tender/Details/T9", ValueError("boom"))
    message = describe(PARTIAL, 2)[2]
    assert "T9: error while scraping" in message and "GO1" not in message


def test_a_page_address_used_as_a_tender_name_is_shortened():
    run_notes.note_reason(run_notes.PORTAL)
    run_notes.note_tender("https://www.tenders.act.gov.au/tender/view/4242", PARTIAL)
    assert "4242: documents could not be opened on the portal" in describe(PARTIAL, 1)[2]
