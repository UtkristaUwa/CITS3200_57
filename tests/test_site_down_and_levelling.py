"""
A site that is unreachable gets the whole run again, and a site that stops
answering part-way is given up on after 15 tenders in a row instead of burning
the run's time on the rest. (eTender's own run-level tests are in its test_cases.)
"""

import logging

import httpx
import pytest

from error_scrapers import common, run_notes
from error_scrapers.grant_connect import scraper as grantconnect

log = logging.getLogger("test")


@pytest.fixture(autouse=True)
def _clean():
    run_notes.reset()
    yield
    run_notes.reset()


# --- retry_if_site_unreachable ----------------------------------------------

def _runs(*results):
    seq = list(results)
    calls = []

    def run_once():
        calls.append(1)
        return seq[min(len(calls), len(seq)) - 1]
    run_once.calls = calls
    return run_once


def test_an_unreachable_site_is_tried_again_after_each_wait(monkeypatch):
    sleeps = []
    monkeypatch.setattr(common.time, "sleep", lambda s: sleeps.append(s))
    run = _runs((common.SITE_TOTAL_FAILURE, []), (common.SITE_TOTAL_FAILURE, []), (common.SITE_SUCCESS, [{"a": 1}]))
    code, tenders = common.retry_if_site_unreachable(run, log)
    assert code == common.SITE_SUCCESS and tenders == [{"a": 1}]
    assert len(run.calls) == 3
    assert sleeps == [60, 180]


def test_it_gives_up_after_the_last_wait_and_returns_the_failure(monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    run = _runs((common.SITE_TOTAL_FAILURE, []))
    assert common.retry_if_site_unreachable(run, log) == (common.SITE_TOTAL_FAILURE, [])
    assert len(run.calls) == 3


@pytest.mark.parametrize("code", [
    common.SITE_LOGIN_FAILED, common.SITE_BOT_BLOCKED, common.SITE_STRUCTURE_CHANGE,
    common.SITE_RATE_LIMITED, common.TENDER_PARTIAL, common.SITE_SUCCESS])
def test_nothing_but_an_unreachable_site_is_retried(monkeypatch, code):
    monkeypatch.setattr(common.time, "sleep", lambda s: pytest.fail("must not wait"))
    run = _runs((code, []))
    assert common.retry_if_site_unreachable(run, log) == (code, [])
    assert len(run.calls) == 1


def test_a_failure_after_some_tenders_were_scraped_is_not_run_again(monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: pytest.fail("must not wait"))
    run = _runs((common.SITE_TOTAL_FAILURE, [{"a": 1}]))
    common.retry_if_site_unreachable(run, log)
    assert len(run.calls) == 1


def test_no_waits_means_no_retry():
    run = _runs((common.SITE_TOTAL_FAILURE, []))
    common.retry_if_site_unreachable(run, log, waits=())
    assert len(run.calls) == 1


# --- DeadSiteWatch ------------------------------------------------------------

def test_fifteen_timeouts_in_a_row_stop_the_run():
    watch = common.DeadSiteWatch()
    for _ in range(14):
        assert watch.failed(httpx.ReadTimeout("slow")) is False
    assert watch.failed(httpx.ReadTimeout("slow")) is True and watch.stopped


def test_one_answer_starts_the_count_again():
    watch = common.DeadSiteWatch()
    for _ in range(14):
        watch.failed(httpx.ConnectError("down"))
    watch.succeeded()
    for _ in range(14):
        assert watch.failed(httpx.ConnectError("down")) is False


def test_a_failure_that_is_not_a_dead_site_resets_the_count():
    watch = common.DeadSiteWatch()
    for _ in range(14):
        watch.failed(httpx.ReadTimeout("slow"))
    assert watch.failed(ValueError("a bug in one tender")) is False
    assert watch.streak == 0


def test_a_server_error_counts_but_a_403_does_not():
    request = httpx.Request("GET", "https://x")
    five = httpx.HTTPStatusError("e", request=request, response=httpx.Response(503, request=request))
    four = httpx.HTTPStatusError("e", request=request, response=httpx.Response(404, request=request))
    watch = common.DeadSiteWatch(limit=2)
    assert watch.failed(five) is False and watch.failed(five) is True
    watch = common.DeadSiteWatch(limit=2)
    assert watch.failed(four) is False and watch.failed(four) is False


def test_the_report_goes_on_the_dashboard():
    watch = common.DeadSiteWatch(limit=1)
    watch.failed(httpx.ReadTimeout("slow"))
    watch.report(log, 20, 300)
    assert "the site stopped answering part-way through" in str(run_notes.snapshot().partials)


# --- GrantConnect: a 429 on a tender stops the run -----------------------------

def test_grantconnect_stops_on_a_429_instead_of_working_through_every_tender(monkeypatch, tmp_path):
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    monkeypatch.setattr(grantconnect, "login", lambda client: True)
    monkeypatch.setattr(grantconnect, "collect_all_listing_urls",
                        lambda client, limit=0, problems=None: [f"https://gc/{i}" for i in range(30)])
    seen = []

    def scrape(client, url, output_dir):
        seen.append(url)
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("slow down", request=request,
                                    response=httpx.Response(429, request=request))
    monkeypatch.setattr(grantconnect, "scrape_opportunity", scrape)
    monkeypatch.setattr(common, "RUN_RETRY_WAITS", ())
    result = grantconnect.run_scraper(output_dir=str(tmp_path))
    assert result.code == common.SITE_RATE_LIMITED
    assert len(seen) == 1


def test_grantconnect_gives_up_on_a_dead_site(monkeypatch, tmp_path):
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    monkeypatch.setattr(grantconnect, "login", lambda client: True)
    monkeypatch.setattr(grantconnect, "collect_all_listing_urls",
                        lambda client, limit=0, problems=None: [f"https://gc/{i}" for i in range(40)])
    seen = []

    def scrape(client, url, output_dir):
        seen.append(url)
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(grantconnect, "scrape_opportunity", scrape)
    result = grantconnect.run_scraper(output_dir=str(tmp_path))
    assert len(seen) == common.DEAD_SITE_AFTER
    assert result.code == common.TENDER_PARTIAL
