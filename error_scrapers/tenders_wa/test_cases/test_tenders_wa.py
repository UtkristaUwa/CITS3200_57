"""
Tests for the Tenders WA scraper, same discipline as
error_scrapers/nt_qtol/test_cases/test_nt_qtol_scraper.py.

All four fixtures are live captures from a logged-out browser:
  tenders_wa_listings.html       the Advertised list (every tender on one page)
  tenders_wa_details.html        one tender (EOIDFES250526, four documents)
  tenders_wa_download_info.html  the page "Download Now" leads to
  tenders_wa_file_download.html  the page "Download for Information Only" leads to
The document zip itself is built in-test, and the fake portal answers each
step the way the real one did when the pages were captured.
"""

import io
import os
import re
import zipfile
from urllib.parse import parse_qs

import docx
import fitz
import httpx
import pytest

from error_scrapers import common, run_notes
from error_scrapers.tenders_wa import scraper

FIXTURES = os.path.dirname(__file__)

BASE = "https://www.tenders.wa.gov.au"
DETAIL_URL = f"{BASE}/watenders/tender/display/tender-details.action?id=70532&action=display-tender-details"
DOWNLOAD_URL = f"{BASE}/watenders/tender/downloadspecs.action?id=70532&method=downloadViaHTML"
REFERENCE = "EOIDFES250526"
LIST_NONCE = "LISTNONCE123"


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def _listing() -> str:
    return _read_fixture("tenders_wa_listings.html")


def _detail() -> str:
    return _read_fixture("tenders_wa_details.html")


def _structure_changed() -> str:
    return _detail().replace('class="subtitle"', 'class="subtitle-CHANGED"')


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _docx_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    document = docx.Document()
    document.add_paragraph(text)
    document.save(buf)
    return buf.getvalue()


def _package_zip() -> bytes:
    """The four documents the detail fixture advertises, as the site would zip them."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{REFERENCE} - Executive Leadership and Management Program.DOCX",
                    _docx_bytes("Executive leadership program"))
        zf.writestr(f"{REFERENCE} - Attachment 1 - Proposed Unit Direction and Key Topics.PDF",
                    _pdf_bytes("Proposed unit direction"))
        zf.writestr(f"{REFERENCE} - Attachment 2 - Process Terms and Conditions.DOCX",
                    _docx_bytes("Process terms"))
        zf.writestr(f"{REFERENCE} - Addendum 1.DOCX", _docx_bytes("Addendum one"))
    return buf.getvalue()


def _portal(package=None, detail_html=None, download_page=None, documents_page=None,
            home_has_menu=True, list_html=None):
    """
    A fake Tenders WA. Answers the home page, the Advertised list, any tender's
    detail page (reference rewritten per id so tenders do not share a folder),
    and the three download steps. `package` is what the final POST returns:
    bytes (a zip), an httpx.Response, or None for the real fixture's four files.
    """
    state = {"requests": [], "posts": []}
    package = _package_zip() if package is None else package

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        state["requests"].append((request.method, str(request.url)))
        if path == "/watenders/index.action":
            menu = (f'<a href="/watenders/tender/search/tender-search.action?'
                    f'action=advanced-tender-search-open-tender&CSRFNONCE={LIST_NONCE}">Advertised</a>'
                    f'<a href="/watenders/tender/search/tender-search.action?'
                    f'action=advanced-tender-search-open-tender-dcsp&CSRFNONCE=WRONG">DCSP</a>'
                    if home_has_menu else "")
            return httpx.Response(200, text=f"<html><body>{menu}</body></html>")
        if path == "/watenders/tender/search/tender-search.action":
            state["list_query"] = str(request.url.query, "utf-8") if isinstance(request.url.query, bytes) else str(request.url.query)
            return httpx.Response(200, text=list_html if list_html is not None else _listing())
        if path == "/watenders/tender/display/tender-details.action":
            tid = request.url.params.get("id")
            html = detail_html if detail_html is not None else _detail()
            return httpx.Response(200, text=html.replace(REFERENCE, f"WA{tid}"))
        if path == "/watenders/tender/downloadspecs.action":
            return httpx.Response(200, text=download_page or _read_fixture("tenders_wa_download_info.html"))
        if path == "/watenders/login.action" and request.method == "POST":
            form = parse_qs(request.content.decode())
            state["posts"].append(("login.action", form))
            if form.get("action") == ["downloadInfoOnly"]:
                return httpx.Response(200, text=documents_page or _read_fixture("tenders_wa_file_download.html"))
            return httpx.Response(200, text="<html><body>login failed</body></html>")
        if path == "/watenders/tender/request/request-spec-docs.action" and request.method == "POST":
            form = parse_qs(request.content.decode())
            state["posts"].append(("request-spec-docs", form, str(request.url)))
            if isinstance(package, httpx.Response):
                return package
            return httpx.Response(200, content=package, headers={
                "content-type": "application/zip",
                "content-disposition": 'attachment; filename="Tender Documents.zip"'})
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
def _no_pause(monkeypatch):
    monkeypatch.setattr(scraper, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(common, "PAGE_RETRY_SECONDS", 0)
    monkeypatch.setattr(common, "DOWNLOAD_RETRY_SECONDS", 0)
    run_notes.reset()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_finds_every_real_tender_once():
    links = scraper.parse_listing(_listing())
    assert len(links) == 167  # 168 rows, minus the site's own demonstration tender
    assert len(set(links)) == len(links)
    assert all(link.startswith(f"{BASE}/watenders/tender/display/tender-details.action?id=") for link in links)


def test_listing_links_carry_no_session_token():
    assert not any("CSRFNONCE" in link or "returnUrl" in link for link in scraper.parse_listing(_listing()))


def test_the_demonstration_tender_is_skipped():
    assert "id=67609" not in " ".join(scraper.parse_listing(_listing()))  # DEMOTENDER


def test_the_advertised_link_is_read_off_the_home_page_with_its_token():
    home = (f'<a href="/watenders/tender/search/tender-search.action?'
            f'action=advanced-tender-search-open-tender&CSRFNONCE=ABC">Advertised</a>')
    url = scraper.find_list_url(home, f"{BASE}/watenders/index.action")
    assert url.endswith("advanced-tender-search-open-tender&CSRFNONCE=ABC")


def test_the_dcsp_menu_link_is_not_mistaken_for_the_advertised_one():
    home = ('<a href="/x?action=advanced-tender-search-open-tender-dcsp&CSRFNONCE=A">DCSP</a>')
    assert scraper.find_list_url(home) == scraper.LIST_URL


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

def test_success_parses_the_detail_page():
    fields, code = scraper.parse_detail(_detail(), DETAIL_URL)
    assert code == common.SITE_SUCCESS
    assert fields["reference"] == REFERENCE
    assert fields["title"] == "Executive Leadership and Management Program"
    assert fields["type"] == "Expression of Interest"
    assert fields["status"] == "Current"
    assert fields["issued_by"] == "Department of Fire and Emergency Services"
    assert fields["closes"].startswith("Mon, 12 Oct 2026 at 2:30PM")
    assert fields["regions"] == "Perth Metropolitan"
    assert fields["unspsc"].startswith("Training planning")
    assert fields["contacts"] == ["Enquiries: Emily Pointing, (08) 93959456, emily.pointing@dfes.wa.gov.au"]
    assert "Graduate Certificate" in fields["description"]
    assert "<p" not in fields["description"]  # the escaped HTML was unwrapped to text


def test_several_regions_are_kept_apart_not_run_together():
    html = _detail().replace("Perth Metropolitan", "Gascoyne<br>Goldfields/Esperance<br>Mid West<br>Perth Metropolitan")
    fields, _ = scraper.parse_detail(html, DETAIL_URL)
    assert fields["regions"] == "Gascoyne, Goldfields/Esperance, Mid West, Perth Metropolitan"


def test_the_advertised_documents_and_download_link_are_found():
    fields, _ = scraper.parse_detail(_detail(), DETAIL_URL)
    assert len(fields["documents"]) == 4
    assert fields["documents"][0].startswith(f"{REFERENCE} - Executive Leadership")
    assert "(Ver 2 - 14 Sep, 2026)" in fields["documents"][2]
    assert fields["download_url"] == DOWNLOAD_URL


def test_a_fixture_page_with_no_extra_sections_has_none():
    fields, _ = scraper.parse_detail(_detail(), DETAIL_URL)
    assert fields["sections"] == {}  # no stray control text from Responses, no repeats of parsed fields


def test_special_response_instructions_inside_responses_are_kept_without_the_submit_controls():
    html = _detail().replace(
        "You must be logged in to submit a response.",
        "You must be logged in to submit a response.</td></tr><tr><td><strong>Special Response Instructions</strong><br>"
        "Expressions of Interest can be emailed to someone@health.wa.gov.au")
    fields, _ = scraper.parse_detail(html, DETAIL_URL)
    text = fields["sections"]["Responses"]
    assert "Special Response Instructions" in text and "someone@health.wa.gov.au" in text
    assert "logged in" not in text and "Closes" not in text and "Click here to submit" not in text


SITE_VISIT = (
    '<table class="subtitle" style="width:100%"><tr><th class="h2">Site Visit</th></tr></table>'
    '<div><p>A site visit will be held at Hakea Prison on 19 Oct, 2026 8:00 AM.</p>'
    '<table><tr><td>Date</td><td>19 October 2026</td></tr><tr><td>Time</td><td>8:00am</td></tr>'
    '<tr><td>Location</td><td>Hakea Prison<br>Nicholson Road Canning Vale WA</td></tr></table></div>'
)


def test_a_site_visit_section_is_kept_with_its_table_rows_on_one_line_each():
    html = _detail().replace("<hr>", SITE_VISIT + "<hr>", 1)
    fields, _ = scraper.parse_detail(html, DETAIL_URL)
    text = fields["sections"]["Site Visit"]
    assert "A site visit will be held at Hakea Prison" in text
    assert "Date: 19 October 2026" in text and "Time: 8:00am" in text
    assert "Location: Hakea Prison | Nicholson Road Canning Vale WA" in text or "Hakea Prison" in text
    assert "Downloaded documents may require" not in text  # stops at the viewer-links footer


def test_extra_sections_reach_the_page_text_file():
    html = _detail().replace("<hr>", SITE_VISIT + "<hr>", 1)
    fields, _ = scraper.parse_detail(html, DETAIL_URL)
    text = scraper.format_detail_text(fields, DETAIL_URL)
    assert "SITE VISIT:" in text and "Date: 19 October 2026" in text


def test_site_structure_change_when_the_title_block_is_missing():
    fields, code = scraper.parse_detail(_structure_changed(), DETAIL_URL)
    assert code == common.SITE_STRUCTURE_CHANGE
    assert fields == {}


def test_a_tender_with_no_documents_has_no_download_link():
    html = re.sub(r'<table role="presentation" id="documents".*?</table>', "", _detail(), flags=re.S)
    html = html.replace("Available by", "")
    fields, code = scraper.parse_detail(html, DETAIL_URL)
    assert code == common.SITE_SUCCESS
    assert fields["download_url"] is None


def test_page_text_holds_the_fields():
    fields, _ = scraper.parse_detail(_detail(), DETAIL_URL)
    text = scraper.format_detail_text(fields, DETAIL_URL)
    assert "TENDERS WA: Executive Leadership and Management Program" in text
    assert "issued_by: Department of Fire and Emergency Services" in text
    assert "Addendum 1.DOCX" in text
    assert "download_url" not in text


# ---------------------------------------------------------------------------
# Download pages
# ---------------------------------------------------------------------------

def test_the_information_only_step_is_found_and_needs_no_login():
    url, payload = scraper.parse_info_only_form(
        _read_fixture("tenders_wa_download_info.html"), f"{BASE}/watenders/tender/downloadspecs.action")
    assert url == f"{BASE}/watenders/login.action"
    assert payload["action"] == "downloadInfoOnly"
    assert "username" not in payload and "password" not in payload


def test_the_documents_form_has_every_file_ticked_and_the_session_ids():
    url, payload, names = scraper.parse_documents_form(
        _read_fixture("tenders_wa_file_download.html"), f"{BASE}/watenders/login.action")
    assert "request-spec-docs.action" in url and "tenderId=70532" in url and "CSRFNONCE=" in url
    assert payload["action"] == "downloadViaHTML"
    assert payload["psfId"] == "937907"
    assert [payload[f"documentRequested[{i}]"] for i in range(4)] == ["true"] * 4
    assert "selectAll" not in payload
    assert len(names) == 4 and names[3].endswith("Addendum 1.DOCX")


def test_pages_without_the_expected_form_give_none():
    assert scraper.parse_info_only_form("<html></html>", BASE) is None
    assert scraper.parse_documents_form("<html></html>", BASE) is None


# ---------------------------------------------------------------------------
# One tender, end to end
# ---------------------------------------------------------------------------

def test_scrape_opportunity_end_to_end(tmp_path):
    code, tender = scraper.scrape_opportunity(_client(_portal()), DETAIL_URL, str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert tender["tender_id"] == "WA70532"
    names = sorted(a["file_name"] for a in tender["attachments"])
    assert len(names) == 4
    assert any(n.endswith("Addendum 1.DOCX") for n in names)
    folder = tender["folder"]
    assert os.path.exists(os.path.join(folder, "__tender__WA70532.txt"))
    # text was extracted from the documents
    assert any(f.endswith(".txt") and "Addendum" in open(os.path.join(folder, f), encoding="utf-8").read()
               for f in os.listdir(folder) if f != "__tender__WA70532.txt")
    assert not [f for f in os.listdir(folder) if f.endswith((".part", ".zip"))]


def test_page_text_records_the_source_url(tmp_path):
    _, tender = scraper.scrape_opportunity(_client(_portal()), DETAIL_URL, str(tmp_path))
    text = open(os.path.join(tender["folder"], "__tender__WA70532.txt"), encoding="utf-8").read()
    assert f"Detail URL: {DETAIL_URL}" in text


def test_the_three_steps_are_taken_in_order_with_the_right_forms(tmp_path):
    transport = _portal()
    scraper.scrape_opportunity(_client(transport), DETAIL_URL, str(tmp_path))
    steps = transport.state["requests"]
    assert [m for m, _ in steps] == ["GET", "GET", "POST", "POST"]
    assert "downloadspecs.action" in steps[1][1]
    info, files = transport.state["posts"]
    assert info[1]["action"] == ["downloadInfoOnly"]
    assert files[1]["action"] == ["downloadViaHTML"] and files[1]["psfId"] == ["937907"]
    assert [files[1][f"documentRequested[{i}]"] for i in range(4)] == [["true"]] * 4
    assert "CSRFNONCE=8585B1A8455588DBF122E0631BE3A51F" in files[2]


def test_nothing_is_ever_signed_in(tmp_path):
    transport = _portal()
    scraper.scrape_opportunity(_client(transport), DETAIL_URL, str(tmp_path))
    for post in transport.state["posts"]:
        assert "username" not in post[1] and "password" not in post[1]


def test_a_tender_with_no_download_link_is_complete_without_documents(tmp_path):
    html = re.sub(r'<table role="presentation" id="documents".*?</table>', "", _detail(), flags=re.S)
    transport = _portal(detail_html=html)
    code, tender = scraper.scrape_opportunity(_client(transport), DETAIL_URL, str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert tender["attachments"] == []
    assert [m for m, _ in transport.state["requests"]] == ["GET"]  # no download attempted


def test_documents_listed_but_no_download_link_is_partial_with_a_reason(tmp_path):
    # Keep the documents table, take away only the "Download Now" link.
    html = re.sub(r'<a href="[^"]*downloadspecs\.action[^"]*"[^>]*>.*?</a>', "", _detail(), flags=re.S)
    assert "downloadspecs.action" not in html and 'id="documents"' in html
    transport = _portal(detail_html=html)
    code, tender = scraper.scrape_opportunity(_client(transport), DETAIL_URL, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert tender["attachments"] == []
    assert os.path.exists(os.path.join(tender["folder"], "__tender__WA70532.txt"))
    assert (run_notes.PORTAL, None) in run_notes._pending
    assert [m for m, _ in transport.state["requests"]] == ["GET"]  # no download attempted


def test_documents_served_as_a_web_page_give_tender_partial_with_page_text_saved(tmp_path):
    login_page = httpx.Response(200, text="<html>Please sign in</html>", headers={"content-type": "text/html"})
    code, tender = scraper.scrape_opportunity(_client(_portal(package=login_page)), DETAIL_URL, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert tender["attachments"] == []
    assert os.path.exists(os.path.join(tender["folder"], "__tender__WA70532.txt"))
    assert (run_notes.PORTAL, None) in run_notes._pending  # noted for the dashboard chip


def test_a_corrupt_package_gives_tender_partial_and_leaves_no_temp_file(tmp_path):
    bad = httpx.Response(200, content=b"this is not a zip", headers={
        "content-type": "application/zip", "content-disposition": 'attachment; filename="x.zip"'})
    code, tender = scraper.scrape_opportunity(_client(_portal(package=bad)), DETAIL_URL, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert not [f for f in os.listdir(tender["folder"]) if f.endswith(".part")]
    assert any(reason == run_notes.DOWNLOAD for reason, _ in run_notes._pending)


def test_fewer_documents_than_advertised_is_partial(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{REFERENCE} - Addendum 1.DOCX", _docx_bytes("Addendum one"))
    code, tender = scraper.scrape_opportunity(_client(_portal(package=buf.getvalue())), DETAIL_URL, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert len(tender["attachments"]) == 1


def test_a_single_file_instead_of_a_zip_is_stored_as_it_is(tmp_path):
    one = httpx.Response(200, content=_pdf_bytes("Just one file"), headers={
        "content-type": "application/pdf", "content-disposition": 'attachment; filename="Only File.pdf"'})
    code, tender = scraper.scrape_opportunity(_client(_portal(package=one)), DETAIL_URL, str(tmp_path))
    assert [a["file_name"] for a in tender["attachments"]] == ["Only File.pdf"]
    assert code == common.TENDER_PARTIAL  # four were advertised, one arrived


def test_a_docx_sent_alone_is_not_unzipped(tmp_path):
    one = httpx.Response(200, content=_docx_bytes("one doc"), headers={
        "content-type": "application/octet-stream", "content-disposition": 'attachment; filename="Only.docx"'})
    _, tender = scraper.scrape_opportunity(_client(_portal(package=one)), DETAIL_URL, str(tmp_path))
    assert [a["file_name"] for a in tender["attachments"]] == ["Only.docx"]


def test_a_changed_download_page_is_a_structure_change_but_keeps_the_page_text(tmp_path):
    transport = _portal(download_page="<html><body>Something new</body></html>")
    code, tender = scraper.scrape_opportunity(_client(transport), DETAIL_URL, str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE
    assert os.path.exists(os.path.join(tender["folder"], "__tender__WA70532.txt"))


def test_a_changed_documents_page_is_a_structure_change(tmp_path):
    transport = _portal(documents_page="<html><body>Something new</body></html>")
    code, _ = scraper.scrape_opportunity(_client(transport), DETAIL_URL, str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE


def test_structure_change_on_the_detail_page(tmp_path):
    code, tender = scraper.scrape_opportunity(
        _client(_portal(detail_html=_structure_changed())), DETAIL_URL, str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE and tender == {}


def test_a_detail_page_that_times_out_is_asked_for_again(tmp_path):
    base = _portal()
    attempts = {"n": 0}

    def handler(request):
        if request.url.path.endswith("tender-details.action") and attempts["n"] == 0:
            attempts["n"] += 1
            raise httpx.ReadTimeout("slow")
        return base.handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.SITE_SUCCESS and tender


def test_a_download_that_fails_with_a_503_is_tried_again(tmp_path):
    base = _portal()
    attempts = {"n": 0}

    def handler(request):
        if request.url.path.endswith("request-spec-docs.action") and attempts["n"] == 0:
            attempts["n"] += 1
            return httpx.Response(503)
        return base.handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.SITE_SUCCESS and len(tender["attachments"]) == 4
    assert attempts["n"] == 1


# ---------------------------------------------------------------------------
# Whole run
# ---------------------------------------------------------------------------

def test_run_respects_limit_and_reads_the_list_with_the_homepage_token(tmp_path, monkeypatch):
    transport = _portal()
    _patch_client(monkeypatch, transport)
    result = scraper.run_scraper(limit=3, output_dir=str(tmp_path))
    assert result == common.ScrapeResult(common.SITE_SUCCESS, "tenders_wa", 3)
    assert f"CSRFNONCE={LIST_NONCE}" in transport.state["list_query"]
    assert "dcsp" not in transport.state["list_query"]


def test_run_falls_back_to_the_plain_list_url_when_the_home_page_has_no_menu(tmp_path, monkeypatch):
    transport = _portal(home_has_menu=False)
    _patch_client(monkeypatch, transport)
    assert scraper.run_scraper(limit=1, output_dir=str(tmp_path)).count == 1
    assert "CSRFNONCE" not in transport.state["list_query"]


def test_empty_list_page_is_a_structure_change(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal(list_html="<html><body>nothing</body></html>"))
    assert scraper.run_scraper(output_dir=str(tmp_path)).code == common.SITE_STRUCTURE_CHANGE


def test_site_total_failure_on_unreachable_url(tmp_path, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("down")
    _patch_client(monkeypatch, httpx.MockTransport(handler))
    assert scraper.run_scraper(output_dir=str(tmp_path)).code == common.SITE_TOTAL_FAILURE


@pytest.mark.parametrize("status, expected", [
    (429, common.SITE_RATE_LIMITED),
    (403, common.SITE_BOT_BLOCKED),
])
def test_429_and_403_on_the_home_page_stop_the_run_without_retry(status, expected, tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status)
    _patch_client(monkeypatch, httpx.MockTransport(handler))
    assert scraper.run_scraper(output_dir=str(tmp_path)).code == expected
    assert len(calls) == 1


@pytest.mark.parametrize("blocked_path, status, expected", [
    ("tender-details.action", 403, common.SITE_BOT_BLOCKED),
    ("tender-details.action", 429, common.SITE_RATE_LIMITED),
    ("request-spec-docs.action", 429, common.SITE_RATE_LIMITED),
    ("downloadspecs.action", 403, common.SITE_BOT_BLOCKED),
])
def test_429_and_403_mid_run_stop_it(blocked_path, status, expected, tmp_path, monkeypatch):
    base = _portal()

    def handler(request):
        if request.url.path.endswith(blocked_path):
            return httpx.Response(status)
        return base.handler(request)
    _patch_client(monkeypatch, httpx.MockTransport(handler))
    assert scraper.run_scraper(limit=3, output_dir=str(tmp_path)).code == expected


def test_one_failed_tender_does_not_stop_the_rest(tmp_path, monkeypatch):
    base = _portal()

    def handler(request):
        if request.url.path.endswith("tender-details.action") and request.url.params.get("id") == "70737":
            return httpx.Response(404)
        return base.handler(request)
    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = scraper.run_scraper(limit=3, output_dir=str(tmp_path))
    assert result.code == common.TENDER_PARTIAL
    assert result.count == 2


def test_run_scraper_returns_a_scrape_result(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal())
    result = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert isinstance(result, common.ScrapeResult)
    assert result.site == "tenders_wa"