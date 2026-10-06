"""
Tests for the AusTender scraper, same discipline as
error_scrapers/buy_nsw/test_cases/test_buynsw.py.

austender_public_list.html, austender_full_details.html and
austender_login_redirect.html are live captures (the last is what an
anonymous request for an ATM's documents page is redirected to).
austender_documents.html is the logged-in documents page, reproduced from
its documented markup since it can't be captured without an account.
The fixtures are trimmed (no scripts/styles, listing cut to 3 ATMs). The
structure-changed page is built at runtime by renaming every "list-desc" class.

Network is faked with httpx.MockTransport rather than a hand-rolled client,
so the real streaming download path is what gets exercised.
"""

import os

import fitz
import httpx
import pytest

from error_scrapers import common
from error_scrapers.austender import scraper

FIXTURES = os.path.dirname(__file__)

DETAIL_URL = "https://www.tenders.gov.au/Atm/Show/a64467be-bb82-4228-85e7-5009fe1f162c"


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def _structure_changed() -> str:
    return _read_fixture("austender_full_details.html").replace("list-desc", "list-desc-CHANGED")


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _docx_bytes(text: str) -> bytes:
    import io
    import docx
    document = docx.Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _signed_in(html: str) -> str:
    """The listing as a signed-in user gets it: the header carries a Logout link."""
    return html.replace("</body>", '<a class="nav-logout" href="#">Logout</a></body>')


def _portal(documents_html=None, file_bytes=None, file_status=200, password_ok=None,
            session_lapses_after=None):
    """
    A fake tenders.gov.au.

    documents_html=None means the documents page redirects to the login form,
    as it does for an anonymous visitor. password_ok=True/False makes the login
    form real: a POST signs the session in (True) or leaves it anonymous
    (False), signed-in listings carry the Logout link, and the documents page
    is only served to a signed-in session. session_lapses_after=N drops the
    session after N documents-page requests, as a timed-out session does.
    """
    file_bytes = _pdf_bytes("Statement of requirements") if file_bytes is None else file_bytes
    state = {"signed_in": False, "documents_served": 0, "logins": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/RegisteredUser/Login" and request.method == "POST":
            state["logins"] += 1
            state["documents_served"] = 0
            state["signed_in"] = bool(password_ok)
            return httpx.Response(302, headers={"Location": "/atm"})
        if path == "/atm":
            if request.url.params.get("page") in (None, "1"):
                html = _read_fixture("austender_public_list.html")
                return httpx.Response(200, text=_signed_in(html) if state["signed_in"] else html)
            return httpx.Response(200, text="<html><body>No results</body></html>")
        if path.startswith("/Atm/Show/"):
            return httpx.Response(200, text=_read_fixture("austender_full_details.html"))
        if path.startswith("/Atm/ViewDocuments/"):
            served = documents_html is not None and (password_ok is None or state["signed_in"])
            if served and session_lapses_after is not None \
                    and state["documents_served"] >= session_lapses_after:
                state["signed_in"] = False
                served = False
            if not served:
                return httpx.Response(
                    302, headers={"Location": f"/RegisteredUser/Login?ReturnUrl={path}"}
                )
            state["documents_served"] += 1
            return httpx.Response(200, text=documents_html)
        if path == "/RegisteredUser/Login":
            return httpx.Response(200, text=_read_fixture("austender_login_redirect.html"))
        if path.startswith("/Atm/Download"):
            if request.url.params.get("fileName", "").lower().endswith(".docx"):
                return httpx.Response(file_status, content=_docx_bytes("Approach to market"))
            return httpx.Response(
                file_status, content=file_bytes, headers={"content-type": "application/pdf"}
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    transport.state = state
    return transport


def _client(transport):
    return httpx.Client(transport=transport, follow_redirects=True)


def _patch_client(monkeypatch, transport):
    real_client = httpx.Client
    monkeypatch.setattr(
        scraper.httpx, "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


@pytest.fixture(autouse=True)
def _no_pause_no_credentials(monkeypatch):
    monkeypatch.setattr(scraper, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(common, "PAGE_RETRY_SECONDS", 0)
    monkeypatch.delenv("AUSTENDER_USERNAME", raising=False)
    monkeypatch.delenv("AUSTENDER_PASSWORD", raising=False)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_finds_atm_links():
    links = scraper.parse_listing(_read_fixture("austender_public_list.html"))
    assert len(links) == 3
    assert all("/Atm/Show/" in link for link in links)


def test_listing_links_are_absolute_and_deduplicated():
    # every ATM is linked twice on the listing (reference and "Full Details")
    links = scraper.parse_listing(_read_fixture("austender_public_list.html"))
    assert all(link.startswith("https://www.tenders.gov.au/") for link in links)
    assert len(links) == len(set(links))


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def test_success_parses_full_details_page():
    fields, code = scraper.parse_detail(_read_fixture("austender_full_details.html"))
    assert code == common.SITE_SUCCESS
    assert fields["title"].startswith("Review of Minimum Energy Performance Standards (MEPS)")
    assert fields["atm_id"] == "ATM_2026_7057"
    assert fields["agency"] == "Department of Climate Change, Energy, the Environment and Water"
    assert fields["close_date"] == "5-Oct-2026 2:00 pm (ACT Local Time)"
    assert fields["publish_date"] == "10-Sep-2026"


def test_site_structure_change_when_fields_container_missing():
    fields, code = scraper.parse_detail(_structure_changed())
    assert code == common.SITE_STRUCTURE_CHANGE
    assert fields == {}


def test_finds_the_documents_page_link():
    url = scraper.find_documents_url(_read_fixture("austender_full_details.html"))
    assert url == "https://www.tenders.gov.au/Atm/ViewDocuments/bcd79820-adc0-4f22-b8ad-6c9058cc1f68"


def test_missing_documents_link_does_not_crash():
    html = _read_fixture("austender_full_details.html").replace("/Atm/ViewDocuments/", "/Atm/Nothing/")
    assert scraper.find_documents_url(html) is None


def test_login_redirect_is_recognised_as_a_login_page():
    assert scraper.is_login_page(_read_fixture("austender_login_redirect.html"))
    assert not scraper.is_login_page(_read_fixture("austender_full_details.html"))


def test_signed_in_pages_are_told_apart_from_anonymous_ones():
    assert scraper.is_signed_in(_read_fixture("austender_full_details.html"))
    assert not scraper.is_signed_in(_read_fixture("austender_public_list.html"))
    assert not scraper.is_signed_in(_read_fixture("austender_login_redirect.html"))


def test_documents_page_names_come_from_title_query_or_fallback():
    documents = scraper.parse_documents(_read_fixture("austender_documents.html"))
    assert [d["file_name"] for d in documents] == [
        "Approach to Market -ATM_2026_7057.docx",
        "Draft Contract - Review of Minimum Energy Performance Standards for Air Conditioners.docx",
        "ATM_2026_7057 Addendum 1.docx",
        "ATM_2026_7057 - Addendum 2.pdf",
        "ATM_2026_6026 - Addendum 3.pdf",
        "ATM_2026_7057 Addendum 3.pdf",
        "ATM_2026_7057 Addendum 4.pdf",
    ]
    assert all(d["url"].startswith("https://www.tenders.gov.au/Atm/Download") for d in documents)


# ---------------------------------------------------------------------------
# Full ATM scrape
# ---------------------------------------------------------------------------

DOCS_ONE_PDF = """
<html><body>
<a title="Statement of Requirements.pdf"
   href="/Atm/DownloadSoftCopy/a64467be?fileName=Statement%20of%20Requirements.pdf">Download</a>
</body></html>
"""


def test_scrape_opportunity_end_to_end(tmp_path):
    with _client(_portal(DOCS_ONE_PDF)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))

    assert code == common.SITE_SUCCESS
    assert tender["tender_id"] == "ATM_2026_7057"
    assert tender["source_url"] == DETAIL_URL
    assert [a["file_name"] for a in tender["attachments"]] == ["Statement of Requirements.pdf"]
    assert tender["attachments"][0]["size_bytes"] > 0

    saved = sorted(os.listdir(tender["folder"]))
    assert saved == [
        "Statement of Requirements.pdf",
        "Statement of Requirements.pdf.txt",
        "__tender__ATM_2026_7057.txt",
    ]
    page_text = open(os.path.join(tender["folder"], "__tender__ATM_2026_7057.txt")).read()
    assert "Review of Minimum Energy Performance Standards" in page_text
    extracted = open(os.path.join(tender["folder"], "Statement of Requirements.pdf.txt")).read()
    assert "Statement of requirements" in extracted


def test_documents_behind_login_give_tender_partial_with_page_text_saved(tmp_path):
    with _client(_portal(documents_html=None)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))

    assert code == common.TENDER_PARTIAL
    assert tender["documents_gated"] is True
    assert tender["attachments"] == []
    assert os.listdir(tender["folder"]) == ["__tender__ATM_2026_7057.txt"]


def test_no_documents_page_is_success_with_no_attachments(tmp_path):
    html = _read_fixture("austender_full_details.html").replace("/Atm/ViewDocuments/", "/Atm/Nothing/")

    def handler(request):
        return httpx.Response(200, text=html)

    with _client(httpx.MockTransport(handler)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert tender["attachments"] == []


def test_a_failed_download_gives_tender_partial_not_a_crash(tmp_path):
    with _client(_portal(DOCS_ONE_PDF, file_status=500)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert tender["attachments"] == []


def test_a_corrupt_pdf_is_kept_but_reported_partial(tmp_path):
    with _client(_portal(DOCS_ONE_PDF, file_bytes=b"not a pdf")) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    # the original is still kept -- someone can open it by hand
    assert [a["file_name"] for a in tender["attachments"]] == ["Statement of Requirements.pdf"]


def test_structure_change_on_detail_page_returns_no_tender(tmp_path):
    def handler(request):
        return httpx.Response(200, text=_structure_changed())

    with _client(httpx.MockTransport(handler)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE
    assert tender == {}


# ---------------------------------------------------------------------------
# Site-level results
# ---------------------------------------------------------------------------

def test_run_scraper_respects_limit(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal(DOCS_ONE_PDF))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert len(tenders) == 1
    assert tenders[0]["attachments"]


def test_run_without_credentials_is_partial_when_documents_are_gated(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal(documents_html=None))
    code, tenders = scraper._scrape_site(limit=2, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert len(tenders) >= 1


def test_run_with_credentials_that_do_not_take_is_login_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("AUSTENDER_USERNAME", "someone@example.com")
    monkeypatch.setenv("AUSTENDER_PASSWORD", "not-a-real-password")
    _patch_client(monkeypatch, _portal(documents_html=None))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_LOGIN_FAILED
    # the page text is still worth keeping
    assert len(tenders) == 1


def test_empty_first_listing_page_is_a_structure_change(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html><body>nothing</body></html>")
    ))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE
    assert tenders == []


def test_site_total_failure_on_unreachable_url(monkeypatch):
    def exploding_get(*args, **kwargs):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx.Client, "get", exploding_get)
    code, tenders = scraper._scrape_site()
    assert code == common.SITE_TOTAL_FAILURE
    assert tenders == []


def test_rate_limited_on_429(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(429)))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_RATE_LIMITED


def test_403_is_bot_blocked_and_stops_the_run(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(403)))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_BOT_BLOCKED
    assert tenders == []


def test_429_on_a_detail_page_stops_the_run(tmp_path, monkeypatch):
    def handler(request):
        if request.url.path == "/atm":
            if request.url.params.get("page") == "1":
                return httpx.Response(200, text=_read_fixture("austender_public_list.html"))
            return httpx.Response(200, text="<html><body>No results</body></html>")
        return httpx.Response(429)

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    code, tenders = scraper._scrape_site(output_dir=str(tmp_path))
    assert code == common.SITE_RATE_LIMITED


def test_run_scraper_returns_a_scrape_result(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal(DOCS_ONE_PDF))
    result = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert isinstance(result, common.ScrapeResult)
    assert (result.code, result.site, result.count) == (common.SITE_SUCCESS, "austender", 1)


def test_page_text_records_the_source_url(tmp_path):
    with _client(_portal(DOCS_ONE_PDF)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert common.read_source_url(tender["folder"]) == DETAIL_URL


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

DOCS_REAL = None  # filled lazily: the real captured documents page


def _docs_real():
    return _read_fixture("austender_documents.html")


def _set_credentials(monkeypatch):
    monkeypatch.setenv("AUSTENDER_USERNAME", "someone@example.com")
    monkeypatch.setenv("AUSTENDER_PASSWORD", "not-a-real-password")


def test_login_is_confirmed_by_the_logout_link():
    with _client(_portal(_docs_real(), password_ok=True)) as client:
        _set_credentials(pytest.MonkeyPatch())
        assert scraper.login(client) is True


def test_a_login_that_does_not_take_is_reported_false():
    with _client(_portal(_docs_real(), password_ok=False)) as client:
        _set_credentials(pytest.MonkeyPatch())
        assert scraper.login(client) is False


def test_signed_in_run_downloads_every_document(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    _patch_client(monkeypatch, _portal(_docs_real(), password_ok=True))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert len(tenders) == 1
    assert len(tenders[0]["attachments"]) == 7


def test_wrong_password_is_login_failed_but_page_text_is_kept(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    transport = _portal(_docs_real(), password_ok=False)
    _patch_client(monkeypatch, transport)
    code, tenders = scraper._scrape_site(limit=2, output_dir=str(tmp_path))
    assert code == common.SITE_LOGIN_FAILED
    assert len(tenders) == 2
    assert all(t["attachments"] == [] for t in tenders)
    assert transport.state["logins"] == 1  # a failed login is never retried


def test_a_lapsed_session_signs_in_again_and_carries_on(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    transport = _portal(_docs_real(), password_ok=True, session_lapses_after=1)
    _patch_client(monkeypatch, transport)
    code, tenders = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert all(len(t["attachments"]) == 7 for t in tenders)
    assert transport.state["logins"] >= 2  # first sign-in plus at least one re-login


def test_relogins_are_capped(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    transport = _portal(_docs_real(), password_ok=True, session_lapses_after=0)
    _patch_client(monkeypatch, transport)
    code, tenders = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.SITE_LOGIN_FAILED
    assert transport.state["logins"] == 1 + scraper.MAX_RELOGINS



# ---------------------------------------------------------------------------
# Contact details
# ---------------------------------------------------------------------------

def test_the_contact_box_is_captured_from_the_detail_page():
    fields, code = scraper.parse_detail(_read_fixture("austender_full_details.html"))
    assert code == common.SITE_SUCCESS
    assert fields["contact_name"] == "GEMS Product Review"
    assert fields["contact_email"] == "GEMSProductReview@dcceew.gov.au"
    assert fields["contact_web"].startswith("https://www.energyrating.gov.au/")


def test_a_contact_with_only_a_name_and_email_is_captured():
    box = (
        '<div class="contact-long"><p class="contact-heading">Contact Details</p>'
        "<p>Naomi Picker</p>"
        '<p><span><label for="EmailAddress">Email Address</label>:</span><br/>'
        '<a class="u email" href="mailto:tenders@nhmrc.gov.au">tenders@nhmrc.gov.au</a></p></div>'
    )
    html = _read_fixture("austender_full_details.html")
    html = html.replace('<div class="contact-long">', "<!--x--><div class=\"gone\">")  # drop both real boxes
    html = html.replace("</body>", box + "</body>")
    fields, _ = scraper.parse_detail(html)
    assert fields["contact_name"] == "Naomi Picker"
    assert fields["contact_email"] == "tenders@nhmrc.gov.au"
    assert "contact_web" not in fields


def test_a_phone_line_is_captured_under_contact_phone():
    box = (
        '<div class="contact-long"><p class="contact-heading">Contact Details</p><p>A Person</p>'
        '<p><span><label for="Phone">Phone Number</label>:</span> 02 6200 0000</p></div>'
    )
    html = _read_fixture("austender_full_details.html").replace('<div class="contact-long">', '<div class="gone">')
    fields, _ = scraper.parse_detail(html.replace("</body>", box + "</body>"))
    assert fields["contact_phone"] == "02 6200 0000"


def test_a_page_without_a_contact_box_still_parses():
    html = _read_fixture("austender_full_details.html").replace('class="contact-long"', 'class="other"')
    fields, code = scraper.parse_detail(html)
    assert code == common.SITE_SUCCESS
    assert "contact_name" not in fields


def test_the_contact_reaches_the_page_text():
    fields, _ = scraper.parse_detail(_read_fixture("austender_full_details.html"))
    text = scraper.format_detail_text(fields, "https://example/atm")
    assert "contact_name: GEMS Product Review" in text
    assert "contact_email: GEMSProductReview@dcceew.gov.au" in text

# ---------------------------------------------------------------------------
# Timeouts are retried; a 429 never is
# ---------------------------------------------------------------------------

def _flaky_detail(failures, error=httpx.ReadTimeout):
    """A portal whose detail page raises `error` the first `failures` times."""
    inner = _portal(DOCS_ONE_PDF)
    calls = {"detail": 0}

    def handler(request):
        if request.url.path.startswith("/Atm/Show/"):
            calls["detail"] += 1
            if calls["detail"] <= failures:
                raise error("The read operation timed out", request=request)
        return inner.handler(request)

    transport = httpx.MockTransport(handler)
    transport.calls = calls
    return transport


def test_a_page_that_times_out_once_is_asked_for_again(tmp_path):
    transport = _flaky_detail(failures=1)
    with _client(transport) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))

    assert code == common.SITE_SUCCESS
    assert tender["tender_id"] == "ATM_2026_7057"
    assert transport.calls["detail"] == 2


def test_a_page_that_keeps_timing_out_gives_up_after_three_attempts(tmp_path):
    transport = _flaky_detail(failures=99)
    with _client(transport) as client, pytest.raises(httpx.ReadTimeout):
        scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))

    assert transport.calls["detail"] == common.PAGE_ATTEMPTS == 3


def test_retries_wait_longer_each_time(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "PAGE_RETRY_SECONDS", 5)
    waits = []
    monkeypatch.setattr(common.time, "sleep", waits.append)
    transport = _flaky_detail(failures=2)
    with _client(transport) as client:
        scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))

    # (the pause between document downloads also sleeps; only the retry waits matter here)
    assert [w for w in waits if w] == [5, 10]


def test_a_429_is_never_retried(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(429)

    with _client(httpx.MockTransport(handler)) as client, pytest.raises(httpx.HTTPStatusError):
        scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))

    assert len(calls) == 1


def test_one_tender_that_times_out_is_partial_and_the_run_carries_on(tmp_path, monkeypatch):
    inner = _portal(DOCS_ONE_PDF)
    detail_calls = {"n": 0}

    def handler(request):
        if request.url.path.startswith("/Atm/Show/"):
            detail_calls["n"] += 1
            if detail_calls["n"] <= common.PAGE_ATTEMPTS:  # the whole first tender
                raise httpx.ReadTimeout("The read operation timed out", request=request)
        return inner.handler(request)

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    code, tenders = scraper._scrape_site(output_dir=str(tmp_path))

    assert code == common.TENDER_PARTIAL
    assert len(tenders) >= 1  # the later tenders were still scraped
