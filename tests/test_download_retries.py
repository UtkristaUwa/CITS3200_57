"""
Retries added for transient failures, checked with fake servers (no network):

  * a document download that fails the way a busy portal does is made once more
    (shared by GrantConnect, Austender, QLD and NT);
  * GrantConnect signs in again when its session lapses;
  * QLD starts a fresh Chrome if the first one will not start.

Never retried: a 403/429/404, a login page served in place of a file, a
sign-in that did not take.
"""

import contextlib
import io
import os
import pathlib
import zipfile

import httpx
import pytest

from error_scrapers import common, run_notes
from error_scrapers.grant_connect import scraper as gc
from error_scrapers.nt_qtol import scraper as nt
from error_scrapers.qld_qtenders import scraper as qld

GC_PAGES = pathlib.Path(__file__).parent.parent / "error_scrapers" / "grant_connect" / "test_cases"


@pytest.fixture(autouse=True)
def _clean_run_notes():
    """These tests run scrapers, which record incomplete tenders in shared state."""
    run_notes.reset()
    yield
    run_notes.reset()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(common, "DOWNLOAD_RETRY_SECONDS", 0)
    monkeypatch.setattr(common, "PAGE_RETRY_SECONDS", 0)
    monkeypatch.setattr(qld, "BROWSER_START_WAIT_SECONDS", 0)


class _DropsMidFile(httpx.SyncByteStream):
    """A body that sends a few bytes and then loses the connection."""

    def __iter__(self):
        yield b"half a fi"
        raise httpx.ReadError("connection reset by peer")


def _server(*answers, headers=None):
    """
    A client that answers each request with the next of `answers`: an int is a
    status with a small body, bytes are a 200 body, "drop" loses the connection
    mid-file. The last answer repeats.
    """
    calls = []

    def handler(request):
        calls.append(str(request.url))
        answer = answers[min(len(calls), len(answers)) - 1]
        if answer == "drop":
            return httpx.Response(200, stream=_DropsMidFile())
        if isinstance(answer, bytes):
            return httpx.Response(200, content=answer, headers=headers or {})
        return httpx.Response(answer, text="nope")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.calls = calls
    return client


# --- the shared download -------------------------------------------------------

def test_a_connection_lost_mid_file_is_downloaded_again_and_leaves_no_half_file(tmp_path):
    client = _server("drop", b"the whole file")

    path, _ = common.stream_attachment(client, "https://x.test/a.pdf", str(tmp_path), "a.pdf")

    assert open(path, "rb").read() == b"the whole file"
    assert sorted(os.listdir(tmp_path)) == ["a.pdf"]     # no "a (1).pdf", no leftover
    assert len(client.calls) == 2


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_server_error_on_a_download_is_tried_once_more(tmp_path, status):
    client = _server(status, b"data")

    path, _ = common.stream_attachment(client, "https://x.test/a.pdf", str(tmp_path), "a.pdf")

    assert open(path, "rb").read() == b"data"
    assert len(client.calls) == 2


def test_a_download_that_keeps_failing_gives_up_after_one_retry_with_nothing_left(tmp_path):
    client = _server("drop")

    with pytest.raises(httpx.ReadError):
        common.stream_attachment(client, "https://x.test/a.pdf", str(tmp_path), "a.pdf")

    assert len(client.calls) == common.DOWNLOAD_ATTEMPTS == 2
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize("status", [403, 404, 429])
def test_a_download_the_portal_refuses_is_not_retried(tmp_path, status):
    client = _server(status)

    with pytest.raises(httpx.HTTPStatusError):
        common.stream_attachment(client, "https://x.test/a.pdf", str(tmp_path), "a.pdf")

    assert len(client.calls) == 1


def test_a_login_page_served_in_place_of_the_file_is_not_retried(tmp_path):
    client = _server(b"<html>sign in</html>", headers={"content-type": "text/html"})

    with pytest.raises(common.DocumentGatedError):
        common.stream_attachment(client, "https://x.test/a.pdf", str(tmp_path), "a.pdf")

    assert len(client.calls) == 1
    assert os.listdir(tmp_path) == []


def test_download_attachment_uses_the_retry_for_austender_and_qld(tmp_path):
    client = _server(503, b"plain text body")

    attachment, extracted = common.download_attachment(
        client, "https://x.test/notes.txt", str(tmp_path), "notes.txt")

    assert attachment["file_name"] == "notes.txt" and attachment["size_bytes"] == len(b"plain text body")
    assert len(client.calls) == 2


# --- GrantConnect's own download goes through the same path --------------------

def test_grantconnect_downloads_a_document_again_after_a_dropped_connection(tmp_path):
    client = _server("drop", b"%PDF- not really")

    code, attachments = gc.process_documents(
        client, [{"url": "https://x.test/doc", "file_name": "tender.zzz"}], str(tmp_path))

    assert code == common.SITE_SUCCESS
    assert [a["file_name"] for a in attachments] == ["tender.zzz"]
    assert len(client.calls) == 2


# --- NT's zip ------------------------------------------------------------------

def _zip_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("spec.txt", "the specification")
    return buffer.getvalue()


def test_nt_downloads_its_zip_again_after_a_server_error_and_leaves_no_zip_behind(tmp_path):
    client = _server(502, _zip_bytes())

    code, attachments, gated = nt.download_bundle(client, "https://x.test/zip", str(tmp_path))

    assert gated is False and code == common.SITE_SUCCESS
    assert [a["file_name"] for a in attachments] == ["spec.txt"]
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".zip")]
    assert len(client.calls) == 2


def test_nt_zip_cut_off_twice_fails_and_leaves_nothing_behind(tmp_path):
    client = _server("drop")

    with pytest.raises(httpx.ReadError):
        nt.download_bundle(client, "https://x.test/zip", str(tmp_path))

    assert os.listdir(tmp_path) == []
    assert len(client.calls) == 2


# --- GrantConnect signs in again when the session lapses -----------------------

def _page(name):
    return (GC_PAGES / f"{name}.html").read_text(encoding="utf-8", errors="replace")


def test_a_signed_out_page_is_recognised_from_the_saved_pages():
    assert gc.signed_out(_page("grantconnect_login_failure")) is True
    assert gc.signed_out(_page("grantconnect_public_list")) is True
    assert gc.signed_out(_page("grantconnect_full_details")) is False
    assert gc.signed_out(_page("grantconnect_attachements")) is False


def test_a_detail_page_that_is_the_sign_in_page_raises_session_lapsed(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=_page("grantconnect_public_list"))))

    with pytest.raises(gc.SessionLapsedError):
        gc.scrape_opportunity(client, "https://www.grants.gov.au/Go/Show?GoUuid=abc", str(tmp_path))


@pytest.fixture
def site(monkeypatch):
    """_scrape_site with the network replaced; `state` scripts what scrape_opportunity does."""
    state = {"logins": 0, "login_ok": [True], "lapses": {}, "scraped": []}

    monkeypatch.setattr(gc.httpx, "Client", lambda **kw: contextlib.nullcontext(object()))
    monkeypatch.setattr(gc, "collect_all_listing_urls", lambda client, limit, problems=None: state["urls"])

    def login(client):
        state["logins"] += 1
        outcomes = state["login_ok"]
        return outcomes[min(state["logins"], len(outcomes)) - 1]
    monkeypatch.setattr(gc, "login", login)

    def scrape_opportunity(client, url, output_dir):
        if state["lapses"].get(url, 0) > 0:
            state["lapses"][url] -= 1
            raise gc.SessionLapsedError("signed out")
        state["scraped"].append(url)
        return common.SITE_SUCCESS, {"tender_id": url}
    monkeypatch.setattr(gc, "scrape_opportunity", scrape_opportunity)
    return state


def test_a_lapsed_session_signs_in_again_and_the_tender_is_redone(site, tmp_path):
    site["urls"] = ["u1", "u2", "u3"]
    site["lapses"] = {"u2": 1}

    code, tenders = gc._scrape_site(output_dir=str(tmp_path))

    assert code == common.SITE_SUCCESS
    assert site["scraped"] == ["u1", "u2", "u3"]
    assert site["logins"] == 2          # the first sign-in, then one after the lapse


def test_re_logins_are_capped_and_the_run_carries_on_with_the_other_tenders(site, tmp_path):
    site["urls"] = ["u1", "u2", "u3", "u4", "u5"]
    site["lapses"] = {url: 99 for url in site["urls"][:4]}   # signed out on every try

    code, tenders = gc._scrape_site(output_dir=str(tmp_path))

    assert site["logins"] == 1 + gc.MAX_RELOGINS      # never more than three re-logins
    assert site["scraped"] == ["u5"]
    assert code == common.TENDER_PARTIAL              # four tenders not done


def test_a_re_login_that_is_rejected_stops_the_run_and_keeps_what_was_scraped(site, tmp_path):
    site["urls"] = ["u1", "u2", "u3"]
    site["lapses"] = {"u2": 1}
    site["login_ok"] = [True, False]     # signs in at the start, refused the second time

    code, tenders = gc._scrape_site(output_dir=str(tmp_path))

    assert code == common.SITE_LOGIN_FAILED
    assert [t["tender_id"] for t in tenders] == ["u1"]
    assert site["logins"] == 2           # the login is not repeated after a refusal


# --- QLD starts a fresh Chrome if the first will not start ---------------------

class _Session:
    starts = 0
    fail_first = 0

    def __init__(self, headless=True):
        pass

    def __enter__(self):
        type(self).starts += 1
        if type(self).starts <= type(self).fail_first:
            raise RuntimeError("chrome not reachable")
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def session_class(monkeypatch):
    cls = type("Session", (_Session,), {"starts": 0, "fail_first": 0})
    monkeypatch.setattr(qld, "VendorPanelSession", cls)
    return cls


def test_a_browser_that_will_not_start_is_started_again(session_class):
    session_class.fail_first = 2
    with contextlib.ExitStack() as stack:
        session = qld.open_vendorpanel(stack)

    assert isinstance(session, session_class) and session_class.starts == 3


def test_a_browser_that_never_starts_gives_up_after_its_attempts(session_class):
    session_class.fail_first = 99
    with contextlib.ExitStack() as stack:
        with pytest.raises(RuntimeError, match="chrome not reachable"):
            qld.open_vendorpanel(stack)

    assert session_class.starts == qld.BROWSER_START_ATTEMPTS


def test_a_sign_in_that_does_not_take_is_not_repeated_after_the_browser_retry(
        session_class, monkeypatch, tmp_path):
    session_class.fail_first = 1
    logins = []
    session_class.login = lambda self: (logins.append(1), False)[1]
    monkeypatch.setattr(qld, "credentials", lambda: ("user", "pass"))
    monkeypatch.setattr(qld, "collect_all_tenders", lambda client, limit, problems=None: [])
    monkeypatch.setattr(qld.httpx, "Client", lambda **kw: contextlib.nullcontext(object()))

    code, _ = qld._scrape_site(output_dir=str(tmp_path))

    assert code == common.SITE_LOGIN_FAILED
    assert session_class.starts == 2      # the browser was started again
    assert logins == [1]                  # but the sign-in was tried only once


# --- a browser page that fails to load is loaded again (VIC, QLD) --------------
# ACT's browser session already did this (tenders_act/browser.py, GET_ATTEMPTS).

class TimeoutException(Exception):
    """Named like selenium's, which is what the driver raises on a page-load timeout."""


class WebDriverException(Exception):
    """Named like selenium's; Chrome's net errors arrive in its message."""


class _FakeSB:
    """The bits of SeleniumBase that a page load touches."""

    def __init__(self, load_errors=(), title="Open tenders", html="<html><body>ok</body></html>"):
        self.load_errors = list(load_errors)
        self.opens = 0
        self.title = title
        self.html = html

    def _load(self, *args, **kwargs):
        self.opens += 1
        if self.load_errors:
            raise self.load_errors.pop(0)

    uc_open_with_reconnect = _load
    open = _load

    def wait_for_element(self, selector, timeout=None):
        return True

    def get_page_source(self):
        return self.html

    def get_title(self):
        return self.title


@pytest.fixture(autouse=True)
def _no_page_load_waiting(monkeypatch):
    monkeypatch.setattr(common, "PAGE_LOAD_RETRY_SECONDS", 0)


def _vic_session(sb):
    from error_scrapers.vic_buyingfor import scraper as vic
    session = vic.BrowserSession()
    session.sb = sb
    return vic, session


@pytest.mark.parametrize("error", [
    TimeoutException("Message: timeout: Timed out receiving message from renderer"),
    WebDriverException("unknown error: net::ERR_CONNECTION_RESET"),
    WebDriverException("unknown error: net::ERR_EMPTY_RESPONSE"),
])
def test_vic_loads_a_page_again_after_a_timeout_or_reset(error):
    sb = _FakeSB(load_errors=[error])
    _, session = _vic_session(sb)

    assert "ok" in session.get("https://x.test/tenders", wait_selector="a.tender")
    assert sb.opens == 2


def test_vic_gives_up_after_three_loads_that_all_time_out():
    sb = _FakeSB(load_errors=[TimeoutException("t")] * 5)
    _, session = _vic_session(sb)

    with pytest.raises(TimeoutException):
        session.get("https://x.test/tenders")

    assert sb.opens == common.PAGE_LOAD_ATTEMPTS == 3


@pytest.mark.parametrize("error", [
    RuntimeError("boom"),
    WebDriverException("invalid session id"),       # a dead browser: a reload cannot help
])
def test_vic_does_not_reload_for_any_other_failure(error):
    sb = _FakeSB(load_errors=[error])
    _, session = _vic_session(sb)

    with pytest.raises(type(error)):
        session.get("https://x.test/tenders")

    assert sb.opens == 1


def test_vic_never_reloads_a_cloudflare_challenge():
    sb = _FakeSB(title="Just a moment...", html="<html>checking your browser</html>")
    vic, session = _vic_session(sb)

    with pytest.raises(vic.BotBlockedError):
        session.get("https://x.test/tenders")

    assert sb.opens == 1                                  # loaded once, not retried


def test_qld_loads_the_tender_list_again_after_a_timeout():
    sb = _FakeSB(load_errors=[TimeoutException("t")],
                 html="<a href='/logout.axd'>Logout</a><table id='tendersDataTable'></table>")
    session = qld.VendorPanelSession()
    session.sb = sb

    session._open_list()

    assert sb.opens == 2 and session._on_list is True
