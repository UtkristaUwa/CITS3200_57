"""
Tests for the Tenders ACT scraper, written test-first -- every function
here exists to satisfy a specific test, same discipline as
error_scrapers/grant_connect/test_cases/test_grantconnect.py and
error_scrapers/buy_nsw/test_cases/test_buynsw.py.

ACT needs a login step (like GrantConnect) plus a two-stage document
flow buy.nsw didn't have: the tender detail page links to a
"download docs" page listing every document as a checked checkbox,
and *that* page's form is POSTed back (with the selected ids) to
actually receive the zip.
"""

import io
import os
import zipfile

import fitz
import pytest

from error_scrapers import common
from error_scrapers.tenders_act import scraper

FIXTURES = os.path.dirname(__file__)


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(autouse=True)
def act_credentials(monkeypatch):
    """Every test runs with credentials set, so a login test exercises the
    form rather than the missing-credentials short-circuit. Tests about
    missing credentials delete them again."""
    monkeypatch.setenv("ACT_USERNAME", "supplier@example.com")
    monkeypatch.setenv("ACT_PASSWORD", "not-a-real-password")


CLOUDFLARE_CHALLENGE_HTML = (
    '<html lang="en-US" dir="ltr"><head><title>Just a moment...</title>'
    '<meta name="robots" content="noindex,nofollow"></head>'
    '<body><div id="challenge-error-text">Enable JavaScript and cookies to continue</div>'
    '</body></html>'
)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_page_has_no_error():
    html = _read_fixture("act_login_page.html")
    assert scraper.login_failed(html) is False


def test_login_failed_detects_error_message():
    html = _read_fixture("act_login_failed.html")
    assert scraper.login_failed(html) is True


def test_login_form_fields_extracted_correctly():
    html = _read_fixture("act_login_page.html")
    fields = scraper.parse_login_form(html)
    assert fields["_csrf"] == "test-csrf-token"
    assert fields["businessType"] == "SUPPLIER"
    assert fields["tenantCode"] == "act"


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_finds_tender_links():
    html = _read_fixture("act_public_list.html")
    links = scraper.parse_listing(html)
    assert len(links) == 22
    assert all("/tender/view?id=" in link for link in links)


def test_listing_links_are_absolute_and_deduplicated():
    html = _read_fixture("act_public_list.html")
    links = scraper.parse_listing(html)
    assert all(link.startswith("http") for link in links)
    assert len(links) == len(set(links))


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def test_success_parses_full_details_page():
    html = _read_fixture("act_tender_detail.html")
    fields, code = scraper.parse_detail(html)
    assert code == common.SITE_SUCCESS
    assert fields["title"] == (
        "Public Housing Disability Modification and Domestic Violence "
        "Upgrade Services Multi Use List (MUL)"
    )
    assert fields["agency"] == "ACT Government"
    assert fields["tender_code"] == "PIMA0014211"
    assert fields["tender_type"] == "Other Arrangements"


def test_site_structure_change_when_general_section_missing():
    # act_structure_changed_detail.html is act_tender_detail.html with
    # id="opportunityGeneral" renamed to id="opportunityGeneral_CHANGED"
    # -- the exact container parse_detail() selects fields from.
    html = _read_fixture("act_structure_changed_detail.html")
    fields, code = scraper.parse_detail(html)
    assert code == common.SITE_STRUCTURE_CHANGE


def test_finds_the_download_docs_url():
    html = _read_fixture("act_tender_detail.html")
    url = scraper.find_download_docs_url(html)
    assert url is not None
    assert "downloadSpecDocs?tenderId=330643" in url


def test_missing_download_link_does_not_crash():
    html = _read_fixture("act_tender_detail.html").replace(
        "Download Now", "Nothing to see here"
    )
    url = scraper.find_download_docs_url(html)
    assert url is None


# ---------------------------------------------------------------------------
# Download-docs page parsing
# ---------------------------------------------------------------------------

def test_parses_download_form_action_and_hidden_fields():
    html = _read_fixture("act_download_docs_page.html")
    form = scraper.parse_download_form(html)
    assert form["action"] == "/secure/tender/downloadSpecDocs"
    assert form["opportunityId"] == "330643"
    assert form["_csrf"] == "test-csrf-token"


def test_collects_every_checked_document_id():
    html = _read_fixture("act_download_docs_page.html")
    form = scraper.parse_download_form(html)
    # 4 Original Documents + 2 Addenda, all checked by default
    assert len(form["ids"]) == 6
    assert "2522065" in form["ids"]
    assert "2532527" in form["ids"]


def test_site_structure_change_when_checkboxes_missing():
    # act_structure_changed_downloads.html is act_download_docs_page.html
    # with name="ids[]" renamed to name="ids_CHANGED[]" -- the exact
    # attribute parse_download_form() relies on.
    html = _read_fixture("act_structure_changed_downloads.html")
    form = scraper.parse_download_form(html)
    assert form["code"] == common.SITE_STRUCTURE_CHANGE


# ---------------------------------------------------------------------------
# Full opportunity scrape (mocked network)
# ---------------------------------------------------------------------------

def _build_fake_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("MUL_Attachment A -Categories of Service.pdf", b"%PDF-1.4 fake a")
        zf.writestr("PIMA0014211 - Addendum 2.pdf", b"%PDF-1.4 fake b")
    buf.seek(0)
    return buf.read()


class _FakeResponse:
    def __init__(self, text="", content=b"", status_code=200):
        self.text = text
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("error", request=None, response=self)


class _FakeClient:
    """Stands in for httpx.Client -- routes by method + URL."""

    def __init__(self, detail_html, download_docs_html, zip_bytes,
                 login_should_fail=False):
        self.detail_html = detail_html
        self.download_docs_html = download_docs_html
        self.zip_bytes = zip_bytes
        self.login_should_fail = login_should_fail
        self.logged_in = False

    def get(self, url, **kwargs):
        if "downloadSpecDocs" in url:
            return _FakeResponse(text=self.download_docs_html)
        if "/login" in url:
            return _FakeResponse(text=_read_fixture("act_login_page.html"))
        return _FakeResponse(text=self.detail_html)

    def post(self, url, **kwargs):
        if "downloadSpecDocs" in url:
            return _FakeResponse(content=self.zip_bytes)
        if "/login" in url:
            if self.login_should_fail:
                return _FakeResponse(text=_read_fixture("act_login_failed.html"))
            self.logged_in = True
            return _FakeResponse(text="<html>logged in</html>")
        return _FakeResponse()


def test_login_success_returns_true(monkeypatch):
    client = _FakeClient("", "", b"")
    assert scraper.login(client) is True


def test_login_failure_returns_false():
    client = _FakeClient("", "", b"", login_should_fail=True)
    assert scraper.login(client) is False


def test_scrape_opportunity_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper.common, "extract_pdf", lambda path: "fake text")
    detail_html = _read_fixture("act_tender_detail.html")
    download_docs_html = _read_fixture("act_download_docs_page.html")
    client = _FakeClient(detail_html, download_docs_html, _build_fake_zip())

    code, tender = scraper.scrape_opportunity(
        client, "https://www.tenders.act.gov.au/tender/view?id=330643", str(tmp_path)
    )
    assert code == common.SITE_SUCCESS
    assert tender["title"] == (
        "Public Housing Disability Modification and Domestic Violence "
        "Upgrade Services Multi Use List (MUL)"
    )
    assert len(tender["attachments"]) == 2


def test_scrape_opportunity_still_succeeds_with_no_download_link(tmp_path):
    detail_html = _read_fixture("act_tender_detail.html").replace(
        "Download Now", "Nothing to see here"
    )
    client = _FakeClient(detail_html, "", b"")
    code, tender = scraper.scrape_opportunity(
        client, "https://www.tenders.act.gov.au/tender/view?id=330643", str(tmp_path)
    )
    assert code == common.SITE_SUCCESS
    assert tender["attachments"] == []


def test_a_corrupt_zip_download_gives_tender_partial_not_a_crash(tmp_path):
    detail_html = _read_fixture("act_tender_detail.html")
    download_docs_html = _read_fixture("act_download_docs_page.html")
    client = _FakeClient(detail_html, download_docs_html, b"not a valid zip")

    code, tender = scraper.scrape_opportunity(
        client, "https://www.tenders.act.gov.au/tender/view?id=330643", str(tmp_path)
    )
    assert code == common.TENDER_PARTIAL


# ---------------------------------------------------------------------------
# Site-level failures
# ---------------------------------------------------------------------------

def test_site_login_failed_stops_the_run():
    client = _FakeClient("", "", b"", login_should_fail=True)
    code, tenders = scraper.run_scraper(client_override=client)
    assert code == common.SITE_LOGIN_FAILED
    assert tenders == []


def test_site_total_failure_on_unreachable_url(monkeypatch):
    import httpx

    def exploding_get(*args, **kwargs):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx.Client, "get", exploding_get)
    monkeypatch.setattr(httpx.Client, "post", exploding_get)
    code, tenders = scraper.run_scraper()
    assert code == common.SITE_TOTAL_FAILURE
    assert tenders == []


# ---------------------------------------------------------------------------
# Missing credentials and Cloudflare
# ---------------------------------------------------------------------------

def test_missing_credentials_fail_without_contacting_the_site(monkeypatch):
    monkeypatch.delenv("ACT_USERNAME")
    monkeypatch.delenv("ACT_PASSWORD")

    class NoNetworkClient:
        def get(self, *args, **kwargs):
            raise AssertionError("login should not have touched the network")
        post = get

    assert scraper.login(NoNetworkClient()) is False


def test_browser_run_with_missing_credentials_never_opens_a_browser(monkeypatch):
    monkeypatch.delenv("ACT_USERNAME")
    calls = []
    monkeypatch.setattr(scraper, "_run_once", lambda *a, **kw: calls.append(1))

    code, site, count = scraper.run_scraper_via_browser()

    assert code == common.SITE_LOGIN_FAILED
    assert count == 0
    assert calls == []


def test_cloudflare_challenge_page_is_recognised():
    assert scraper.is_blocked("Just a moment...", CLOUDFLARE_CHALLENGE_HTML) is True


def test_real_login_page_is_not_mistaken_for_a_block():
    html = _read_fixture("act_login_page.html")
    assert scraper.is_blocked("Tenders ACT", html) is False


class _FakeSB:
    """Stands in for SeleniumBase's SB: every page is Cloudflare's challenge."""

    def __init__(self):
        self.opens = 0

    def uc_open_with_reconnect(self, url, reconnect_time=None):
        self.opens += 1

    def wait_for_element(self, selector, timeout=None):
        raise RuntimeError(f"{selector} never appeared")

    def get_title(self):
        return "Just a moment..."

    def get_page_source(self):
        return CLOUDFLARE_CHALLENGE_HTML

    def get_current_url(self):
        return scraper.LOGIN_URL


def test_browser_get_gives_up_on_the_first_challenge(tmp_path):
    from error_scrapers.tenders_act.browser import BrowserSession

    session = BrowserSession(download_dir=str(tmp_path))
    session.sb = _FakeSB()

    with pytest.raises(scraper.BotBlockedError):
        session.get(scraper.LOGIN_URL, wait_selector="#supplierUsername")
    assert session.sb.opens == 1


def test_a_challenge_at_login_is_reported_as_bot_blocked(tmp_path, monkeypatch):
    from error_scrapers.tenders_act import browser

    class BlockedSession:
        def __init__(self, download_dir):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def login(self):
            raise scraper.BotBlockedError("Cloudflare challenge instead of /login")

    monkeypatch.setattr(browser, "BrowserSession", BlockedSession)

    code, tenders = scraper._run_once(output_dir=str(tmp_path))

    assert code == common.SITE_BOT_BLOCKED
    assert tenders == []


def test_bot_blocked_is_retried_with_a_fresh_browser_then_reported(monkeypatch):
    attempts = []

    def blocked_run(limit, output_dir):
        attempts.append(1)
        return common.SITE_BOT_BLOCKED, []

    monkeypatch.setattr(scraper, "_run_once", blocked_run)
    monkeypatch.setattr(scraper.time, "sleep", lambda seconds: None)

    code, site, count = scraper.run_scraper_via_browser(attempts=3)

    assert code == common.SITE_BOT_BLOCKED
    assert len(attempts) == 3


def test_a_structure_change_is_not_retried(monkeypatch):
    attempts = []

    def changed_run(limit, output_dir):
        attempts.append(1)
        return common.SITE_STRUCTURE_CHANGE, []

    monkeypatch.setattr(scraper, "_run_once", changed_run)

    code, _site, _count = scraper.run_scraper_via_browser(attempts=3)

    assert code == common.SITE_STRUCTURE_CHANGE
    assert len(attempts) == 1


# ---------------------------------------------------------------------------
# Signed-in detection, the listing's own count, and the browser run
# ---------------------------------------------------------------------------

def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _offered_ids() -> int:
    return len(scraper.parse_download_form(_read_fixture("act_download_docs_page.html"))["ids"])


def _zip_of(count: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in range(count):
            zf.writestr(f"Document {n}.pdf", _pdf_bytes(f"document {n}"))
    return buf.getvalue()


def test_signed_in_pages_are_recognised():
    assert scraper.is_signed_in(_read_fixture("act_tender_detail.html")) is True
    assert scraper.is_signed_in(_read_fixture("act_login_page.html")) is False


def test_listing_reports_its_own_record_count():
    html = _read_fixture("act_public_list.html")
    assert scraper.listing_record_count(html) == len(scraper.parse_listing(html)) == 22
    assert scraper.listing_record_count("<html></html>") is None


class FakeBrowserSession:
    """Stands in for BrowserSession: serves the real captured pages."""

    def __init__(self, tmp_path, zip_bytes, download_page=None, lapse_after=None,
                 relogin_works=True):
        self.tmp_path = tmp_path
        self.zip_bytes = zip_bytes
        self.download_page = download_page or _read_fixture("act_download_docs_page.html")
        self.lapse_after = lapse_after
        self.relogin_works = relogin_works
        self.signed_in = False
        self.logins = 0
        self.details_served = 0

    def __call__(self, download_dir=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def login(self):
        self.logins += 1
        if self.logins > 1 and not self.relogin_works:
            return False
        self.signed_in = True
        self.details_served = 0
        return True

    def get(self, url, wait_selector=None, attempts=3):
        if url == scraper.LIST_URL:
            return _read_fixture("act_public_list.html")
        if "downloadSpecDocs" in url:
            return self.download_page
        self.details_served += 1
        if self.lapse_after is not None and self.details_served > self.lapse_after:
            self.signed_in = False
        html = _read_fixture("act_tender_detail.html")
        return html if self.signed_in else html.replace("href=\"/logout\"", "href=\"/login\"")

    def download_via_form(self, url, ids):
        path = os.path.join(str(self.tmp_path), "package.zip")
        with open(path, "wb") as f:
            f.write(self.zip_bytes)
        return path


def _run_with(monkeypatch, tmp_path, session, limit=2):
    from error_scrapers.tenders_act import browser

    monkeypatch.setattr(browser, "BrowserSession", session)
    return scraper._run_once(limit=limit, output_dir=str(tmp_path / "out"))


def test_every_document_unpacked_is_a_success(tmp_path, monkeypatch):
    session = FakeBrowserSession(tmp_path, _zip_of(_offered_ids()))
    code, tenders = _run_with(monkeypatch, tmp_path, session)
    assert code == common.SITE_SUCCESS
    assert len(tenders) == 2
    assert len(tenders[0]["attachments"]) == _offered_ids()


def test_fewer_files_than_offered_is_partial(tmp_path, monkeypatch):
    session = FakeBrowserSession(tmp_path, _zip_of(max(_offered_ids() - 1, 0)))
    code, tenders = _run_with(monkeypatch, tmp_path, session)
    assert code == common.TENDER_PARTIAL
    assert len(tenders) == 2  # the tenders are still kept


def test_a_download_page_with_no_usable_form_is_partial_not_success(tmp_path, monkeypatch):
    session = FakeBrowserSession(
        tmp_path, _zip_of(1), download_page=_read_fixture("act_structure_changed_downloads.html"))
    code, tenders = _run_with(monkeypatch, tmp_path, session)
    assert code == common.TENDER_PARTIAL
    assert len(tenders) == 2


def test_an_unreadable_document_is_partial(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in range(_offered_ids()):
            zf.writestr(f"Document {n}.pdf", b"not really a pdf")
    session = FakeBrowserSession(tmp_path, buf.getvalue())
    code, _tenders = _run_with(monkeypatch, tmp_path, session)
    assert code == common.TENDER_PARTIAL


def test_a_lapsed_session_signs_in_again_and_carries_on(tmp_path, monkeypatch):
    session = FakeBrowserSession(tmp_path, _zip_of(_offered_ids()), lapse_after=1)
    code, tenders = _run_with(monkeypatch, tmp_path, session, limit=3)
    assert code == common.SITE_SUCCESS
    assert len(tenders) == 3
    assert session.logins >= 2


def test_a_failed_relogin_stops_the_run_as_login_failed(tmp_path, monkeypatch):
    session = FakeBrowserSession(tmp_path, _zip_of(_offered_ids()), lapse_after=0,
                                 relogin_works=False)
    code, tenders = _run_with(monkeypatch, tmp_path, session, limit=3)
    assert code == common.SITE_LOGIN_FAILED
    assert tenders == []
    assert session.logins == 2  # the first sign-in plus one refused re-login, never more


def test_relogins_are_capped(tmp_path, monkeypatch):
    session = FakeBrowserSession(tmp_path, _zip_of(_offered_ids()), lapse_after=0)
    code, _tenders = _run_with(monkeypatch, tmp_path, session, limit=10)
    assert session.logins <= 1 + scraper.MAX_RELOGINS
    assert code == common.SITE_LOGIN_FAILED


class _FakeLoginSB:
    """SeleniumBase stand-in for the login page."""

    def __init__(self, after_submit_html):
        self.after_submit_html = after_submit_html
        self.submitted = False

    def uc_open_with_reconnect(self, url, reconnect_time=None):
        pass

    def wait_for_element(self, selector, timeout=None):
        pass

    def type(self, selector, text):
        pass

    def click(self, selector):
        self.submitted = True

    def get_title(self):
        return "Tenders ACT"

    def get_current_url(self):
        return scraper.BASE_URL + "/tenders/open"  # moved off /login either way

    def get_page_source(self):
        return self.after_submit_html if self.submitted else _read_fixture("act_login_page.html")


def _login_with(monkeypatch, tmp_path, after_submit_html):
    from error_scrapers.tenders_act import browser

    monkeypatch.setattr(browser.time, "sleep", lambda seconds: None)
    session = browser.BrowserSession(download_dir=str(tmp_path))
    session.sb = _FakeLoginSB(after_submit_html)
    return session.login()


def test_browser_login_succeeds_only_when_the_logout_link_appears(tmp_path, monkeypatch):
    assert _login_with(monkeypatch, tmp_path, _read_fixture("act_tender_detail.html")) is True


def test_browser_login_fails_on_the_wrong_password_page(tmp_path, monkeypatch):
    assert _login_with(monkeypatch, tmp_path, _read_fixture("act_login_failed.html")) is False


def test_browser_login_is_not_assumed_from_leaving_the_login_url(tmp_path, monkeypatch):
    # The page moved off /login but shows no Log Out link: not a confirmed sign-in.
    assert _login_with(monkeypatch, tmp_path, "<html><body>Welcome</body></html>") is False