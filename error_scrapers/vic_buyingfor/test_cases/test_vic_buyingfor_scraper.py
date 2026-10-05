"""
Tests for the Buying for Victoria scraper, same discipline as
error_scrapers/tenders_act/test_cases/test_act.py (the same tender platform).

Live captures, trimmed (no scripts/styles/search dropdowns; the listing cut
to 3 rows): vic_public_list.html, vic_full_details.html (PROCF22-000236, four
documents, SIGNED-IN view), vic_full_details_anonymous.html (PROCF23-149, ten
documents, anonymous view), vic_downloads.html (the signed-in document form),
vic_logout.html / vic_wrong_pass.html (the login page after logging out / a
wrong password) and vic_cloudflare_block.html (what a plain HTTP client
gets). The structure-changed page is built at runtime by renaming the
#opportunityGeneral id.

No browser runs here: FakeSession stands in for BrowserSession, serving
fixture HTML by URL, so the tests need neither Chrome nor network.
"""

import io
import os
import zipfile

import fitz
import pytest

from error_scrapers import common
from error_scrapers.vic_buyingfor import scraper

FIXTURES = os.path.dirname(__file__)

DETAIL_URL = "https://www.tenders.vic.gov.au/tender/view?id=285775"
ENTRY = {
    "url": DETAIL_URL,
    "rfx": "PROCF23-149",
    "title": "Clinical Advisory Services Register",
    "opening_date": "Mon, 24 February 2025 5:00 pm",
    "closing_date": "Mon, 25 February 2030 5:00 pm",
}


# Small captured pages embedded so the tests need no extra fixture files; a real
# file in this folder with the same name takes precedence.
_EMBEDDED = {
    'vic_full_details_anonymous.html': (
        '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'
        'Gvr/vkf3xdL8E='
    ),
}


def _read_fixture(name: str) -> str:
    path = os.path.join(FIXTURES, name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    import base64
    import zlib

    return zlib.decompress(base64.b64decode("".join(_EMBEDDED[name]))).decode("utf-8")


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


SIGNED_IN_CODE = "PROCF22-000236"  # the tender on the signed-in capture


def _signed_in_details() -> str:
    """The detail page as a signed-in supplier sees it (live capture)."""
    return _read_fixture("vic_full_details.html")


def _anonymous_details() -> str:
    """The detail page as an anonymous visitor sees it (live capture)."""
    return _read_fixture("vic_full_details_anonymous.html")


def _structure_changed() -> str:
    return _anonymous_details().replace(
        'id="opportunityGeneral"', 'id="opportunityGeneral-CHANGED"'
    )


def _no_documents_details() -> str:
    return _anonymous_details().replace('class="specDoc"', 'class="gone"')


class FakeSession:
    """Stands in for BrowserSession: serves pages by URL, 'downloads' a zip."""

    def __init__(self, detail_html=None, list_html=None, zip_bytes=None,
                 blocked=False, login_ok=True):
        self.detail_html = detail_html or _anonymous_details()
        self.list_html = list_html or _read_fixture("vic_public_list.html")
        self.zip_bytes = zip_bytes
        self.blocked = blocked
        self.login_ok = login_ok
        self.visited = []
        self.logins = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, wait_selector=None):
        self.visited.append(url)
        if self.blocked:
            raise scraper.BotBlockedError(url)
        if "/tenders/open" in url:
            return self.list_html
        return self.detail_html

    def login(self):
        self.logins += 1
        return self.login_ok

    def download_documents(self, url, folder):
        if self.zip_bytes is None:
            raise TimeoutError("no download")
        path = os.path.join(folder, "documents.zip")
        with open(path, "wb") as f:
            f.write(self.zip_bytes)
        return path


def _documents_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Service Specification.pdf", _pdf_bytes("Clinical advisory services"))
        zf.writestr("Invoice Template.xlsx", b"not really a spreadsheet")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _no_pause_no_credentials(monkeypatch):
    monkeypatch.setattr(scraper, "PAUSE_SECONDS", 0)
    monkeypatch.delenv("VIC_USERNAME", raising=False)
    monkeypatch.delenv("VIC_PASSWORD", raising=False)


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(scraper, "BrowserSession", lambda **kwargs: session)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_finds_every_tender_row_with_its_dates():
    entries = scraper.parse_listing(_read_fixture("vic_public_list.html"))
    assert len(entries) == 3
    assert entries[1] == ENTRY


def test_listing_links_are_absolute_and_deduplicated():
    entries = scraper.parse_listing(_read_fixture("vic_public_list.html"))
    urls = [e["url"] for e in entries]
    assert all(u.startswith("https://www.tenders.vic.gov.au/tender/view?id=") for u in urls)
    assert len(urls) == len(set(urls))


def test_page_count_comes_from_the_record_summary():
    # "Records: 1 - 25 of 78"
    assert scraper.total_pages(_read_fixture("vic_public_list.html")) == 4
    assert scraper.total_pages("<html></html>") is None


def test_cloudflare_block_page_is_recognised():
    assert scraper.is_blocked("", _read_fixture("vic_cloudflare_block.html"))
    assert scraper.is_blocked("Just a moment...", "")
    assert not scraper.is_blocked("Current Tenders", _read_fixture("vic_public_list.html"))


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def test_success_parses_full_details_page():
    fields, code = scraper.parse_detail(_anonymous_details())
    assert code == common.SITE_SUCCESS
    assert fields["title"] == "Clinical Advisory Services Register"
    assert fields["tender_code"] == "PROCF23-149"
    assert fields["agency"] == "Transport Accident Commission"
    assert fields["tender_type"] == "Expression of Interest"
    assert fields["tender_status"] == "Open"
    assert fields["contact_email"] == "clinical_register@tac.vic.gov.au"
    assert fields["description"].startswith("The TAC")


def test_signed_in_page_parses_the_same_way():
    fields, code = scraper.parse_detail(_signed_in_details())
    assert code == common.SITE_SUCCESS
    assert fields["title"] == "Road Safety Services Register"
    assert fields["tender_code"] == SIGNED_IN_CODE
    assert fields["agency"] == "Transport Accident Commission"
    assert fields["tender_type"] == "Expression of Interest"
    assert fields["tender_status"] == "Open"


def test_site_structure_change_when_field_section_missing():
    fields, code = scraper.parse_detail(_structure_changed())
    assert code == common.SITE_STRUCTURE_CHANGE
    assert fields == {}


def test_documents_are_listed_even_when_they_cannot_be_downloaded():
    html = _anonymous_details()
    documents = scraper.parse_documents(html)
    assert len(documents) == 10
    assert documents[0] == {
        "file_name": "Invitation to Register - Clinical Advisory Services (Individual).DOCX",
        "version": "Version 1 (24 Feb 2025)",
        "size": "106 KB",
    }
    assert scraper.documents_require_login(html)
    assert scraper.find_download_docs_url(html) is None


def test_signed_in_page_offers_the_download_form():
    html = _signed_in_details()
    url = scraper.find_download_docs_url(html)
    assert url == "https://www.tenders.vic.gov.au/secure/tender/downloadSpecDocs?tenderId=249237"
    assert not scraper.documents_require_login(html)
    assert [d["file_name"] for d in scraper.parse_documents(html)][0] == \
        "0. TAC Safe Driving Policy for Employers.PDF"
    assert len(scraper.parse_documents(html)) == 4


def test_signed_in_and_anonymous_pages_are_told_apart():
    assert scraper.is_signed_in(_signed_in_details())
    assert scraper.is_signed_in(_read_fixture("vic_downloads.html"))
    assert not scraper.is_signed_in(_anonymous_details())
    assert not scraper.is_signed_in(_read_fixture("vic_logout.html"))
    assert not scraper.is_signed_in(_read_fixture("vic_wrong_pass.html"))


def test_the_document_form_is_read_from_the_real_capture():
    form = scraper.parse_download_form(_read_fixture("vic_downloads.html"))
    assert form == {"count": 4, "unchecked": 0}
    unticked = _read_fixture("vic_downloads.html").replace('checked=""', "")
    assert scraper.parse_download_form(unticked) == {"count": 4, "unchecked": 4}
    assert scraper.parse_download_form(_signed_in_details()) is None


# ---------------------------------------------------------------------------
# Full tender scrape
# ---------------------------------------------------------------------------

def test_anonymous_scrape_saves_page_text_and_reports_documents_gated(tmp_path):
    code, tender = scraper.scrape_opportunity(FakeSession(), ENTRY, str(tmp_path))

    assert code == common.TENDER_PARTIAL
    assert tender["tender_id"] == "PROCF23-149"
    assert tender["documents_gated"] is True
    assert tender["attachments"] == []
    assert os.listdir(tender["folder"]) == ["__tender__PROCF23-149.txt"]

    text = open(os.path.join(tender["folder"], "__tender__PROCF23-149.txt")).read()
    assert "closing_date: Mon, 25 February 2030 5:00 pm" in text
    assert "SPECIFICATION DOCUMENTS:" in text
    assert "Service Specification" in text


def test_signed_in_scrape_unpacks_the_document_zip(tmp_path):
    session = FakeSession(detail_html=_signed_in_details(), zip_bytes=_documents_zip())
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))

    # the fake .xlsx can't be read, so the tender is partial -- but kept
    assert code == common.TENDER_PARTIAL
    assert sorted(a["file_name"] for a in tender["attachments"]) == [
        "Invoice Template.xlsx", "Service Specification.pdf",
    ]
    saved = sorted(os.listdir(tender["folder"]))
    assert "documents.zip" not in saved
    assert "Service Specification.pdf.txt" in saved


def test_signed_in_scrape_with_readable_documents_is_success(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Service Specification.pdf", _pdf_bytes("Clinical advisory services"))
    session = FakeSession(detail_html=_signed_in_details(), zip_bytes=buf.getvalue())
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert [a["file_name"] for a in tender["attachments"]] == ["Service Specification.pdf"]


def test_a_failed_download_gives_tender_partial_not_a_crash(tmp_path):
    session = FakeSession(detail_html=_signed_in_details(), zip_bytes=None)
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert os.listdir(tender["folder"]) == [f"__tender__{SIGNED_IN_CODE}.txt"]


def test_no_documents_is_success(tmp_path):
    session = FakeSession(detail_html=_no_documents_details())
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert tender["documents_gated"] is False


def test_structure_change_on_detail_page_returns_no_tender(tmp_path):
    session = FakeSession(detail_html=_structure_changed())
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE
    assert tender == {}


# ---------------------------------------------------------------------------
# Site-level results
# ---------------------------------------------------------------------------

def test_run_scraper_respects_limit(tmp_path, monkeypatch):
    session = FakeSession(detail_html=_no_documents_details())
    _patch_session(monkeypatch, session)
    code, tenders = scraper._scrape_site(limit=2, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert len(tenders) == 2


def test_run_scraper_returns_a_scrape_result(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession())
    result = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert isinstance(result, common.ScrapeResult)
    assert tuple(result) == (common.TENDER_PARTIAL, "vic_buyingfor", 1)


def test_page_text_records_the_source_url(tmp_path):
    _, tender = scraper.scrape_opportunity(FakeSession(), ENTRY, str(tmp_path))
    assert common.read_source_url(tender["folder"]) == ENTRY["url"]


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
        return _read_fixture("vic_cloudflare_block.html")

    def get_current_url(self):
        return scraper.LOGIN_URL


def test_browser_get_gives_up_on_the_first_challenge():
    session = scraper.BrowserSession()
    session.sb = _FakeSB()

    with pytest.raises(scraper.BotBlockedError):
        session.get(scraper.LOGIN_URL, wait_selector="#supplierUsername")
    assert session.sb.opens == 1


def test_a_dead_browser_is_retried_with_a_fresh_one(monkeypatch):
    calls = []

    def run(limit, output_dir, headless):
        calls.append(1)
        return (common.SITE_TOTAL_FAILURE, []) if len(calls) < 3 else (common.SITE_SUCCESS, [{"tender_id": "X"}])

    monkeypatch.setattr(scraper, "_scrape_site", run)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    code, tenders = scraper._scrape_with_retries()
    assert code == common.SITE_SUCCESS
    assert len(calls) == 3


@pytest.mark.parametrize("code", [common.SITE_LOGIN_FAILED, common.SITE_STRUCTURE_CHANGE])
def test_login_failures_and_structure_changes_are_never_retried(monkeypatch, code):
    # Repeated bad logins can lock an account, so a failed login stops at once.
    calls = []
    monkeypatch.setattr(scraper, "_scrape_site",
                        lambda limit, output_dir, headless: (calls.append(1), (code, []))[1])
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    assert scraper._scrape_with_retries() == (code, [])
    assert len(calls) == 1


def test_a_cloudflare_block_is_retried_with_a_fresh_browser_then_reported(monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(
        scraper, "_scrape_site",
        lambda limit, output_dir, headless: (calls.append(1), (common.SITE_BOT_BLOCKED, []))[1])
    monkeypatch.setattr(scraper.time, "sleep", sleeps.append)

    assert scraper._scrape_with_retries() == (common.SITE_BOT_BLOCKED, [])

    assert len(calls) == scraper.BROWSER_ATTEMPTS == 3   # a hard cap, never more
    assert sleeps == [15, 30]                            # backs off, no wait after the last try


def test_a_cloudflare_block_that_clears_on_a_later_attempt_succeeds(monkeypatch):
    calls = []

    def run(limit, output_dir, headless):
        calls.append(1)
        return (common.SITE_BOT_BLOCKED, []) if len(calls) < 2 else (common.SITE_SUCCESS, [{"tender_id": "X"}])

    monkeypatch.setattr(scraper, "_scrape_site", run)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)

    code, tenders = scraper._scrape_with_retries()

    assert code == common.SITE_SUCCESS
    assert len(calls) == 2


def test_a_block_after_some_tenders_were_scraped_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scraper, "_scrape_site",
        lambda limit, output_dir, headless: (calls.append(1), (common.SITE_BOT_BLOCKED, [{"tender_id": "X"}]))[1])
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)

    code, tenders = scraper._scrape_with_retries()

    assert code == common.SITE_BOT_BLOCKED and tenders == [{"tender_id": "X"}]
    assert len(calls) == 1


def test_run_walks_every_reported_page(monkeypatch):
    session = FakeSession()
    entries = scraper.collect_all_listing_entries(session)
    # every fixture page returns the same 3 rows, so page 2 adds nothing
    # new and the walk stops there instead of asking for pages 3 and 4
    assert len(entries) == 3
    assert session.visited == [scraper.LIST_URL, f"{scraper.LIST_URL}?page=2"]


def test_run_without_credentials_is_partial_when_documents_are_gated(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession())
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert len(tenders) == 1


def test_failed_login_is_reported_but_page_text_is_still_scraped(tmp_path, monkeypatch):
    monkeypatch.setenv("VIC_USERNAME", "someone@example.com")
    monkeypatch.setenv("VIC_PASSWORD", "not-a-real-password")
    _patch_session(monkeypatch, FakeSession(login_ok=False))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_LOGIN_FAILED
    assert len(tenders) == 1


def test_cloudflare_block_is_site_bot_blocked(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession(blocked=True))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_BOT_BLOCKED
    assert tenders == []


def test_empty_first_listing_page_is_a_structure_change(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession(list_html="<html><body>nothing</body></html>"))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE


def test_browser_that_will_not_start_is_total_failure(tmp_path, monkeypatch):
    def no_browser(**kwargs):
        raise RuntimeError("chrome not found")

    monkeypatch.setattr(scraper, "BrowserSession", no_browser)
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_TOTAL_FAILURE
    assert tenders == []


# ---------------------------------------------------------------------------
# Signing in, mid-run session lapses, and the document form
# ---------------------------------------------------------------------------

class LapsingSession(FakeSession):
    """Signed in until `lapse_after` detail pages have been served; then pages
    come back as the anonymous view until login() is called again."""

    def __init__(self, lapse_after, relogin_works=True, **kwargs):
        super().__init__(**kwargs)
        self.lapse_after = lapse_after
        self.relogin_works = relogin_works
        self.served = 0
        self.lapsed = False

    def get(self, url, wait_selector=None):
        if "/tenders/open" in url:
            return self.list_html
        if self.lapse_after is not None and self.served >= self.lapse_after:
            self.lapsed = True
        self.served += 1
        return _anonymous_details() if self.lapsed else _signed_in_details()

    def login(self):
        self.logins += 1
        if self.logins > 1 and not self.relogin_works:
            return False
        self.lapsed = False
        self.served = 0
        return True


def _set_credentials(monkeypatch):
    monkeypatch.setenv("VIC_USERNAME", "someone@example.com")
    monkeypatch.setenv("VIC_PASSWORD", "not-a-real-password")


def _readable_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Service Specification.pdf", _pdf_bytes("Road safety services"))
    return buf.getvalue()


def test_signed_in_run_downloads_the_documents_for_every_tender(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    session = LapsingSession(lapse_after=None, zip_bytes=_readable_zip())
    _patch_session(monkeypatch, session)
    code, tenders = scraper._scrape_site(limit=2, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert session.logins == 1


def test_a_lapsed_session_signs_in_again_and_carries_on(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    session = LapsingSession(lapse_after=1, zip_bytes=_readable_zip())
    _patch_session(monkeypatch, session)
    code, tenders = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert session.logins >= 2  # the first sign-in, then at least one re-login


def test_a_failed_relogin_is_not_retried_forever(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    session = LapsingSession(lapse_after=0, relogin_works=False, zip_bytes=_readable_zip())
    _patch_session(monkeypatch, session)
    code, tenders = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert session.logins <= 1 + scraper.MAX_RELOGINS


class _FakeLoginSB:
    """SeleniumBase stand-in for the login page. After the form is submitted it
    serves the real wrong-password page, or a signed-in page."""

    def __init__(self, password_ok):
        self.password_ok = password_ok
        self.submitted = False
        self.typed = {}

    def uc_open_with_reconnect(self, url, reconnect_time=None):
        pass

    def wait_for_element(self, selector, timeout=None):
        pass

    def get_title(self):
        return "Buying For Victoria"

    def get_page_source(self):
        if not self.submitted:
            return _read_fixture("vic_logout.html")
        return _signed_in_details() if self.password_ok else _read_fixture("vic_wrong_pass.html")

    def get_current_url(self):
        return scraper.LOGIN_URL

    def type(self, selector, text):
        self.typed[selector] = text

    def click(self, selector):
        self.submitted = True

    def is_element_present(self, selector):
        return self.submitted and self.password_ok and selector == scraper.SIGNED_IN_SELECTOR


def test_browser_login_succeeds_when_the_logout_link_appears(monkeypatch):
    _set_credentials(monkeypatch)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    session = scraper.BrowserSession()
    session.sb = _FakeLoginSB(password_ok=True)
    assert session.login() is True
    assert session.sb.typed == {"#supplierUsername": "someone@example.com",
                                "#supplierPassword": "not-a-real-password"}


def test_browser_login_fails_on_the_sites_wrong_password_message(monkeypatch):
    _set_credentials(monkeypatch)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    session = scraper.BrowserSession()
    session.sb = _FakeLoginSB(password_ok=False)
    assert session.login() is False


class _FakeDownloadSB(_FakeLoginSB):
    """SeleniumBase stand-in for the document form: clicking Download drops a zip
    into the downloads folder. `page` is the HTML the browser is looking at."""

    def __init__(self, page, downloads_dir):
        super().__init__(password_ok=True)
        self.page = page
        self.downloads_dir = downloads_dir
        self.clicks = []

    def get_page_source(self):
        return self.page

    def click(self, selector):
        self.clicks.append(selector)
        if selector == "#downloadButton":
            with open(os.path.join(self.downloads_dir, "Documents.zip"), "wb") as f:
                f.write(_readable_zip())


def _download_session(tmp_path, page):
    downloads = tmp_path / "downloaded_files"
    downloads.mkdir()
    session = scraper.BrowserSession()
    session._downloads_dir = str(downloads)
    session.sb = _FakeDownloadSB(page, str(downloads))
    return session


def test_download_clicks_the_button_and_moves_the_zip_into_the_tender_folder(tmp_path):
    session = _download_session(tmp_path, _read_fixture("vic_downloads.html"))
    folder = tmp_path / "tender"
    folder.mkdir()
    path = session.download_documents("https://www.tenders.vic.gov.au/secure/tender/downloadSpecDocs?tenderId=1",
                                      str(folder))
    assert os.path.dirname(path) == str(folder) and os.path.exists(path)
    assert session.sb.clicks == ["#downloadButton"]  # every box was already ticked


def test_download_ticks_everything_first_if_the_boxes_are_not_ticked(tmp_path):
    page = _read_fixture("vic_downloads.html").replace('checked=""', "")
    session = _download_session(tmp_path, page)
    folder = tmp_path / "tender"
    folder.mkdir()
    session.download_documents("https://www.tenders.vic.gov.au/x", str(folder))
    assert session.sb.clicks == ["#checkAll", "#downloadButton"]


def test_a_page_without_the_document_form_is_reported_not_clicked(tmp_path):
    session = _download_session(tmp_path, _signed_in_details())  # no #spec form here
    with pytest.raises(common.StructureChangedError):
        session.download_documents("https://www.tenders.vic.gov.au/x", str(tmp_path))
    assert session.sb.clicks == []


def test_a_cloudflare_block_during_a_document_download_stops_the_run(tmp_path, monkeypatch):
    class BlockedOnDownload(FakeSession):
        def download_documents(self, url, folder):
            raise scraper.BotBlockedError(url)

    _patch_session(monkeypatch, BlockedOnDownload(detail_html=_signed_in_details()))
    code, tenders = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.SITE_BOT_BLOCKED
    assert len(tenders) <= 1  # stopped at the first block, did not carry on to the rest