"""
QLD second pass: tenders that could not be followed on the first go get another.

Last night 36 of 80 tenders failed at the first step (the search never narrowed
to their row) while the rest of the run was fine; doing the same search again by
hand worked. retry_unfollowed() tries those tenders once more after the main
loop. No network or browser: the session and fetch_documents are fakes.
"""

import pytest

from error_scrapers import common
from error_scrapers.qld_qtenders import scraper as qld


class FakeSession:
    def __init__(self):
        self.filter_wait = qld.FILTER_WAIT_SECONDS
        self._on_list = True
        self.waits_seen = []


def _result(ref, advertised=3, followed=False, gated=True, **extra):
    return {"tender_id": ref, "documents_advertised": advertised, "followed": followed,
            "documents_gated": gated, "session_lapsed": False, **extra}


@pytest.fixture
def no_sleep():
    return lambda seconds: None


def _fetch_that(outcomes, session, calls):
    """fetch_documents stand-in: outcomes maps tender id -> (followed, code)."""
    def fetch(vendorpanel, result, advertised):
        calls.append(result["tender_id"])
        session.waits_seen.append(vendorpanel.filter_wait)
        assert vendorpanel._on_list is False  # a fresh list each time
        followed, code = outcomes[result["tender_id"]]
        result["followed"] = followed
        return code
    return fetch


def test_a_tender_that_fails_the_first_time_is_followed_on_the_retry(monkeypatch, no_sleep):
    session, calls = FakeSession(), []
    monkeypatch.setattr(qld, "fetch_documents",
                        _fetch_that({"VP1": (True, common.SITE_SUCCESS)}, session, calls))
    results = [_result("VP1")]

    codes = qld.retry_unfollowed(session, results, sleep=no_sleep)

    assert codes == {"VP1": common.SITE_SUCCESS}
    assert results[0]["followed"] is True
    assert calls == ["VP1"]


def test_a_tender_that_still_fails_stays_partial(monkeypatch, no_sleep):
    session, calls = FakeSession(), []
    monkeypatch.setattr(qld, "fetch_documents",
                        _fetch_that({"VP1": (False, common.TENDER_PARTIAL)}, session, calls))

    codes = qld.retry_unfollowed(session, [_result("VP1")], sleep=no_sleep)

    assert codes == {"VP1": common.TENDER_PARTIAL}


def test_followed_tenders_and_tenders_without_documents_are_left_alone(monkeypatch, no_sleep):
    session, calls = FakeSession(), []
    monkeypatch.setattr(qld, "fetch_documents", _fetch_that({}, session, calls))
    results = [_result("VP1", followed=True), _result("VP2", advertised=0),
               _result("VP3", gated=False)]  # all its documents are linked: no follow needed

    assert qld.retry_unfollowed(session, results, sleep=no_sleep) == {}
    assert calls == []


def test_the_retry_is_more_patient_and_restores_the_normal_wait(monkeypatch, no_sleep):
    session, calls = FakeSession(), []
    monkeypatch.setattr(qld, "fetch_documents",
                        _fetch_that({"VP1": (True, common.SITE_SUCCESS)}, session, calls))

    qld.retry_unfollowed(session, [_result("VP1")], sleep=no_sleep)

    assert session.waits_seen == [qld.RETRY_FILTER_WAIT_SECONDS]
    assert session.filter_wait == qld.FILTER_WAIT_SECONDS


def test_the_normal_wait_is_restored_even_if_a_retry_blows_up(monkeypatch, no_sleep):
    session = FakeSession()

    def boom(*args, **kwargs):
        raise RuntimeError("browser died")

    monkeypatch.setattr(qld, "fetch_documents", boom)

    with pytest.raises(RuntimeError):
        qld.retry_unfollowed(session, [_result("VP1")], sleep=no_sleep)

    assert session.filter_wait == qld.FILTER_WAIT_SECONDS


def test_a_failed_direct_download_keeps_the_tender_partial_after_recovery(monkeypatch, no_sleep):
    session, calls = FakeSession(), []
    monkeypatch.setattr(qld, "fetch_documents",
                        _fetch_that({"VP1": (True, common.SITE_SUCCESS)}, session, calls))
    results = [_result("VP1", public_download_failed=True)]

    codes = qld.retry_unfollowed(session, results, sleep=no_sleep)

    assert codes == {"VP1": common.TENDER_PARTIAL}


def test_the_pass_stops_when_its_time_budget_runs_out(monkeypatch, no_sleep):
    session, calls = FakeSession(), []
    outcomes = {f"VP{i}": (True, common.SITE_SUCCESS) for i in range(5)}
    monkeypatch.setattr(qld, "fetch_documents", _fetch_that(outcomes, session, calls))
    ticks = iter([0, 1, 2, 100, 100, 100, 100, 100, 100, 100])

    codes = qld.retry_unfollowed(session, [_result(f"VP{i}") for i in range(5)],
                                 max_seconds=10, sleep=no_sleep, clock=lambda: next(ticks))

    assert len(codes) == 2  # the third check is past the 10s budget
    assert session.filter_wait == qld.FILTER_WAIT_SECONDS


def test_a_lapsed_session_is_signed_in_again_and_the_tender_retried(monkeypatch, no_sleep):
    session, state = FakeSession(), {"calls": 0}

    def fetch(vendorpanel, result, advertised):
        state["calls"] += 1
        if state["calls"] == 1:
            result["session_lapsed"] = True
            return common.TENDER_PARTIAL
        result["followed"] = True
        return common.SITE_SUCCESS

    monkeypatch.setattr(qld, "fetch_documents", fetch)

    codes = qld.retry_unfollowed(session, [_result("VP1")], relogin=lambda: True, sleep=no_sleep)

    assert codes == {"VP1": common.SITE_SUCCESS}
    assert state["calls"] == 2


def test_the_pass_stops_if_the_session_cannot_be_restored(monkeypatch, no_sleep):
    session, state = FakeSession(), {"calls": 0}

    def fetch(vendorpanel, result, advertised):
        state["calls"] += 1
        result["session_lapsed"] = True
        return common.TENDER_PARTIAL

    monkeypatch.setattr(qld, "fetch_documents", fetch)

    codes = qld.retry_unfollowed(session, [_result("VP1"), _result("VP2")],
                                 relogin=lambda: False, sleep=no_sleep)

    assert codes == {}
    assert state["calls"] == 1


def test_find_row_waits_as_long_as_the_session_says(monkeypatch):
    waits = []

    class Browser:
        def type(self, selector, text):
            pass

        def wait_for_element(self, selector, timeout):
            waits.append(timeout)

        def get_page_source(self):
            return "<html></html>"

    session = qld.VendorPanelSession()
    session.sb = Browser()
    session._on_list = True
    monkeypatch.setattr(qld, "parse_row", lambda html, number: {"followed": False})

    session.find_row("1")
    session.filter_wait = 25
    session.find_row("1")

    assert waits == [qld.FILTER_WAIT_SECONDS, 25]
