"""
Tests for the SA Tenders scraper (tenders.sa.gov.au), same discipline as
error_scrapers/vic_buyingfor/test_cases (the same tender platform).

Live captures in this folder: sa_tenders_public_list.html (the Current Tenders
page; its results arrive by AJAX so the saved file has an empty #resultDiv),
sa_tenders_details.html (DPC038672, four documents, SIGNED-IN view),
sa_tenders_download_attachments.html (the signed-in document form),
sa_tenders_login.html / sa_tenders_logout.html / sa_tenders_wrong_login.html /
sa_tenders_login_success.html.

sa_tenders_results_page1.html is the live getGroupedTableAjax response for page 1
of the open tenders (15 rows, "Records: 1 - 15 of 117"). Later pages are built
here from the same row template.

No browser runs here: FakeSession stands in for BrowserSession, serving
fixture HTML by URL, so the tests need neither Chrome nor network.
"""

import io
import os
import zipfile

import fitz
import pytest

from error_scrapers import common
from error_scrapers.sa_tenders import scraper

FIXTURES = os.path.dirname(__file__)

DETAIL_URL = "https://www.tenders.sa.gov.au/tender/view?id=47223"
ENTRY = {"url": DETAIL_URL, "tender_code": "DPC038672", "title": "x",
         "state": "Open", "agency": "Procurement SA", "category": None,
         "closing_date": "16 Sept, 2030"}


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def _row(tender_id, code, title, closing="16 Sept, 2030"):
    return f"""
<tr id="tenderRow{tender_id}" data-tender-code="{code}">
 <td><div><div>
   <a href="/tender/view?id={tender_id}" class="strong tenderRowTitle">
     {title}
   </a><i class="fas fa-paperclip"></i></div>
   <div><span class="line-item-detail">Issued by: Procurement SA</span></div>
   <div><span class="line-item-detail">
     Category:
     Information Technology Broadcasting and Telecommunications
   </span></div>
 </div></td>
 <td><b>{code}</b><br><span class="tender-row-state">Open</span></td>
 <td class="tender-date"><span class="date_label_list">Closing</span><br>
   <span class="closing_date">
      {closing}
   </span></td>
</tr>"""


def _rows(*items):
    return ('<div class="tender-table"><table class="list"><tbody>'
            + "".join(_row(*i) for i in items) + "</tbody></table></div>")


def _pager(first, last, total):
    return (f'<div class="paging"><span>Pages:</span><span class="current">1</span>'
            f'<span style="white-space:nowrap"> Records: {first} - {last} of {total} </span></div>')


def _page(*items, total=None, first=1):
    """A results fragment as the site returns it, with its pager."""
    last = first + len(items) - 1
    return _rows(*items) + _pager(first, last, total or len(items))


def _real_page1() -> str:
    return _read("sa_tenders_results_page1.html")


def _list_page(fragment=None):
    """The Current Tenders page after its AJAX call has filled #resultDiv."""
    fragment = _real_page1() if fragment is None else fragment
    return _read("sa_tenders_public_list.html").replace(
        '<div id="resultDiv"></div>', f'<div id="resultDiv">{fragment}</div>')


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _signed_in_details() -> str:
    return _read("sa_tenders_details.html")


def _anonymous_details() -> str:
    """The same page without the Download Now button, as an anonymous visitor
    would see it (the exact wording the site shows is the VIC wording)."""
    html = _signed_in_details()
    start = html.index('<a href="/secure/tender/downloadSpecDocs')
    end = html.index("</a>", start) + 4
    return html[:start] + "You must be logged in to download documents" + html[end:]


def _no_documents_details() -> str:
    return _signed_in_details().replace('class="specDoc"', 'class="gone"')


def _structure_changed() -> str:
    return _signed_in_details().replace(
        'id="opportunityGeneral"', 'id="opportunityGeneral-CHANGED"')


class FakeSession:
    """Stands in for BrowserSession: serves pages by URL, 'downloads' a zip."""

    def __init__(self, detail_html=None, list_html=None, zip_bytes=None,
                 blocked=False, login_ok=True, pages=None):
        self.detail_html = detail_html or _signed_in_details()
        self.list_html = list_html or _list_page()
        self.zip_bytes = zip_bytes
        self.blocked = blocked
        self.login_ok = login_ok
        self.pages = pages or {}
        self.visited = []
        self.opened_pages = []
        self.logins = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, wait_selector=None):
        self.visited.append(url)
        if self.blocked:
            raise scraper.BotBlockedError(url)
        if "/tender/search" in url:
            return self.list_html
        return self.detail_html

    def open_page(self, page):
        self.opened_pages.append(page)
        return self.pages.get(page, "")

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


def _readable_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Guide.pdf", _pdf_bytes("Panel guide"))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _no_pause_no_credentials(monkeypatch):
    monkeypatch.setattr(scraper, "PAUSE_SECONDS", 0)
    monkeypatch.delenv("SA_USERNAME", raising=False)
    monkeypatch.delenv("SA_PASSWORD", raising=False)


def _set_credentials(monkeypatch):
    monkeypatch.setenv("SA_USERNAME", "someone@example.com")
    monkeypatch.setenv("SA_PASSWORD", "not-a-real-password")


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(scraper, "BrowserSession", lambda **kwargs: session)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_the_saved_list_page_has_no_rows_until_the_ajax_call_fills_it():
    assert scraper.parse_listing(_read("sa_tenders_public_list.html")) == []


def test_the_real_first_page_has_fifteen_rows():
    entries = scraper.parse_listing(_real_page1())
    assert len(entries) == 15
    assert len({e["url"] for e in entries}) == 15


def test_the_first_row_is_read_with_all_its_fields():
    first = scraper.parse_listing(_real_page1())[0]
    assert first == {
        "url": DETAIL_URL, "tender_code": "DPC038672",
        "title": "eProjects Panel and Cyber Security Services Panel notification",
        "state": "Open", "agency": "Procurement SA",
        "category": "Information Technology Broadcasting and Telecommunications",
        "closing_date": "16 Sept, 2030",
    }


def test_a_tender_with_several_categories_keeps_all_of_them():
    entry = next(e for e in scraper.parse_listing(_real_page1()) if e["tender_code"] == "CWT059378")
    assert entry["agency"] == "City of West Torrens"
    assert entry["category"].startswith("Environmental management - 20%; Environmental Services - 20%")
    assert entry["category"].count(";") == 4


def test_the_pager_gives_rows_per_page_and_total():
    assert scraper.listing_counts(_real_page1()) == (15, 117)
    assert scraper.total_pages(_real_page1()) == 8
    assert scraper.listing_counts("<html></html>") is None
    assert scraper.total_pages("<html></html>") == 1
    assert scraper.total_pages(_pager(1, 15, 30)) == 2
    assert scraper.total_pages(_pager(1, 15, 31)) == 3


def test_listing_links_are_absolute_and_deduplicated():
    rows = _rows((1, "A", "a"), (1, "A", "a"), (2, "B", "b"))
    urls = [e["url"] for e in scraper.parse_listing(rows)]
    assert urls == ["https://www.tenders.sa.gov.au/tender/view?id=1",
                    "https://www.tenders.sa.gov.au/tender/view?id=2"]


def test_a_link_outside_a_table_row_is_still_found():
    html = '<div><a class="tenderRowTitle" href="/tender/view?id=9">Nine</a></div>'
    entries = scraper.parse_listing(html)
    assert [e["url"] for e in entries] == ["https://www.tenders.sa.gov.au/tender/view?id=9"]
    assert entries[0]["title"] == "Nine"


def test_cloudflare_block_page_is_recognised():
    assert scraper.is_blocked("Attention Required! | Cloudflare", "")
    assert scraper.is_blocked("Just a moment...", "")
    assert scraper.is_blocked("", "<p>Sorry, you have been blocked</p>")
    assert not scraper.is_blocked("Current Tenders", _read("sa_tenders_public_list.html"))


def test_every_page_is_walked_and_the_rows_combined():
    p1 = _list_page(_page((1, "A", "a"), (2, "B", "b"), total=5))
    session = FakeSession(list_html=p1, pages={
        2: _page((3, "C", "c"), (4, "D", "d"), total=5, first=3),
        3: _page((5, "E", "e"), total=5, first=5),
    })
    entries = scraper.collect_all_listing_entries(session)
    assert [e["tender_code"] for e in entries] == ["A", "B", "C", "D", "E"]
    assert session.opened_pages == [2, 3]


def test_a_single_page_of_results_opens_no_other_page():
    session = FakeSession(list_html=_list_page(_rows((1, "A", "a"))))
    assert len(scraper.collect_all_listing_entries(session)) == 1
    assert session.opened_pages == []


def test_the_limit_stops_the_listing_early():
    session = FakeSession()  # the real first page: 15 rows of 117
    assert len(scraper.collect_all_listing_entries(session, limit=5)) == 5
    assert session.opened_pages == []


def test_a_page_with_nothing_new_stops_the_walk(caplog):
    p1 = _list_page(_page((1, "A", "a"), total=3))
    session = FakeSession(list_html=p1, pages={2: _page((1, "A", "a"), total=3)})
    with caplog.at_level("ERROR"):
        entries = scraper.collect_all_listing_entries(session)
    assert len(entries) == 1
    assert session.opened_pages == [2]
    assert any("only 1 were found" in r.getMessage() for r in caplog.records)


class PagerBreaksAt(FakeSession):
    """A pager that works until `broken_page`, then fails the way the live site did."""

    def __init__(self, broken_page, **kwargs):
        super().__init__(**kwargs)
        self.broken_page = broken_page

    def open_page(self, page):
        if page >= self.broken_page:
            self.opened_pages.append(page)
            raise RuntimeError("javascript error: Cannot read properties of undefined (reading 'page')\n"
                               "Stacktrace:\n#0 0x55 <unknown>")
        return super().open_page(page)


def _three_pages():
    return dict(
        list_html=_list_page(_page((1, "A", "a"), (2, "B", "b"), total=5)),
        pages={2: _page((3, "C", "c"), (4, "D", "d"), total=5, first=3),
               3: _page((5, "E", "e"), total=5, first=5)})


def test_a_page_that_cannot_be_opened_keeps_the_pages_before_it():
    problems = []
    entries = scraper.collect_all_listing_entries(PagerBreaksAt(3, **_three_pages()), problems=problems)
    assert [e["tender_code"] for e in entries] == ["A", "B", "C", "D"]
    assert problems == ["page 3 of 3"]


def test_a_pager_that_fails_on_the_first_page_still_gives_page_one():
    problems = []
    entries = scraper.collect_all_listing_entries(PagerBreaksAt(2, **_three_pages()), problems=problems)
    assert [e["tender_code"] for e in entries] == ["A", "B"]
    assert problems == ["page 2 of 3"]


def test_a_working_pager_reports_no_problem():
    problems = []
    scraper.collect_all_listing_entries(FakeSession(**_three_pages()), problems=problems)
    assert problems == []


def test_a_cloudflare_block_while_opening_a_page_still_ends_the_run():
    class BlockedOnPage2(FakeSession):
        def open_page(self, page):
            raise scraper.BotBlockedError("challenge")

    with pytest.raises(scraper.BotBlockedError):
        scraper.collect_all_listing_entries(BlockedOnPage2(**_three_pages()))


def test_a_broken_pager_still_scrapes_page_one_and_reports_a_structure_change(tmp_path, monkeypatch):
    session = PagerBreaksAt(2, detail_html=_no_documents_details(), **_three_pages())
    _patch_session(monkeypatch, session)
    code, tenders = scraper._scrape_site(output_dir=str(tmp_path))
    assert len(tenders) == 2  # page 1's two tenders were still scraped
    assert code == common.SITE_STRUCTURE_CHANGE


def test_a_broken_pager_is_not_retried_with_a_new_browser_when_tenders_were_scraped(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    sessions = []

    def make_session(**kwargs):
        sessions.append(PagerBreaksAt(2, detail_html=_no_documents_details(), **_three_pages()))
        return sessions[-1]
    monkeypatch.setattr(scraper, "BrowserSession", make_session)
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert result.code == common.SITE_STRUCTURE_CHANGE
    assert result.count == 2
    assert len(sessions) == 1


# ---------------------------------------------------------------------------
# Whole-run retry: a dead browser
# ---------------------------------------------------------------------------

DEAD = "invalid session id: session deleted as the browser has closed the connection"


def _four_tenders():
    return dict(list_html=_list_page(_page((1, "A", "a"), (2, "B", "b"), (3, "C", "c"), (4, "D", "d"),
                                            total=4)))


class DiesOnDetail(FakeSession):
    """Works for `after` detail pages, then the browser is gone for good."""

    def __init__(self, after, **kwargs):
        super().__init__(**kwargs)
        self.after = after

    def get(self, url, wait_selector=None):
        if "/tender/search" not in url and len(self.visited) - 1 >= self.after:
            self.visited.append(url)
            raise RuntimeError(DEAD)
        return super().get(url, wait_selector)


def _sessions(monkeypatch, *makers):
    """Hand out one session per browser start, in order; returns the list made."""
    made = []
    makers = list(makers)

    def make(**kwargs):
        made.append(makers[min(len(made), len(makers) - 1)]())
        return made[-1]
    monkeypatch.setattr(scraper, "BrowserSession", make)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    return made


def test_browser_is_gone_recognises_dead_browsers_only():
    assert scraper.browser_is_gone(RuntimeError(DEAD))
    assert scraper.browser_is_gone(RuntimeError("chrome not reachable"))
    assert scraper.browser_is_gone(RuntimeError("disconnected: not connected to DevTools"))
    assert not scraper.browser_is_gone(TimeoutError("Download did not complete"))
    assert not scraper.browser_is_gone(ValueError("bad page"))


def test_a_browser_that_dies_part_way_is_replaced_and_finishes_the_rest_once(tmp_path, monkeypatch):
    made = _sessions(
        monkeypatch,
        lambda: DiesOnDetail(2, detail_html=_no_documents_details(), **_four_tenders()),
        lambda: FakeSession(detail_html=_no_documents_details(), **_four_tenders()))
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert result.code == common.SITE_SUCCESS
    assert result.count == 4
    assert len(made) == 2
    details = lambda s: [u for u in s.visited if "/tender/search" not in u]
    assert len(details(made[1])) == 2  # only the two the first browser never reached


def test_a_dead_browser_is_not_counted_as_a_failure_of_every_remaining_tender(tmp_path, monkeypatch, caplog):
    _sessions(monkeypatch,
              lambda: DiesOnDetail(1, detail_html=_no_documents_details(), **_four_tenders()),
              lambda: FakeSession(detail_html=_no_documents_details(), **_four_tenders()))
    with caplog.at_level("ERROR"):
        scraper.run_scraper(output_dir=str(tmp_path))
    assert not [r for r in caplog.records if "FAILED" in r.getMessage()]


def test_a_partial_tender_before_the_crash_keeps_the_final_code_partial(tmp_path, monkeypatch):
    # FakeSession() with no zip: the document download raises, so tenders are partial.
    _sessions(monkeypatch,
              lambda: DiesOnDetail(1, **_four_tenders()),
              lambda: FakeSession(detail_html=_no_documents_details(), **_four_tenders()))
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert result.code == common.TENDER_PARTIAL
    assert result.count == 4


def test_it_gives_up_after_three_browsers_and_reports_total_failure(tmp_path, monkeypatch):
    made = _sessions(monkeypatch,
                     lambda: DiesOnDetail(1, detail_html=_no_documents_details(), **_four_tenders()))
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert result.code == common.SITE_TOTAL_FAILURE
    assert len(made) == 3
    assert result.count == 3  # each browser got one more tender before it died; none redone


def test_a_browser_that_dies_while_walking_the_pager_is_retried_not_called_a_structure_change(tmp_path, monkeypatch):
    class PagerDies(FakeSession):
        def open_page(self, page):
            raise RuntimeError(DEAD)

    made = _sessions(monkeypatch,
                     lambda: PagerDies(detail_html=_no_documents_details(), **_three_pages()),
                     lambda: FakeSession(detail_html=_no_documents_details(), **_three_pages()))
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert result.code == common.SITE_SUCCESS
    assert result.count == 5
    assert len(made) == 2


def test_cloudflare_after_tenders_were_scraped_is_not_retried(tmp_path, monkeypatch):
    class BlockedAfterOne(FakeSession):
        def get(self, url, wait_selector=None):
            if "/tender/search" not in url and len(self.visited) >= 2:
                raise scraper.BotBlockedError(url)
            return super().get(url, wait_selector)

    made = _sessions(monkeypatch,
                     lambda: BlockedAfterOne(detail_html=_no_documents_details(), **_four_tenders()))
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert result.code == common.SITE_BOT_BLOCKED
    assert len(made) == 1


def test_a_failed_login_is_never_retried(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    made = _sessions(monkeypatch,
                     lambda: FakeSession(login_ok=False, detail_html=_no_documents_details(),
                                         **_four_tenders()))
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert result.code == common.SITE_LOGIN_FAILED
    assert len(made) == 1


def test_a_complete_walk_logs_no_error(caplog):
    p1 = _list_page(_page((1, "A", "a"), total=2))
    session = FakeSession(list_html=p1, pages={2: _page((2, "B", "b"), total=2, first=2)})
    with caplog.at_level("ERROR"):
        scraper.collect_all_listing_entries(session)
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_an_empty_listing_is_a_structure_change():
    session = FakeSession(list_html=_read("sa_tenders_public_list.html"))
    with pytest.raises(common.StructureChangedError):
        scraper.collect_all_listing_entries(session)


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def test_success_parses_full_details_page():
    fields, code = scraper.parse_detail(_signed_in_details())
    assert code == common.SITE_SUCCESS
    assert fields["title"] == "eProjects Panel and Cyber Security Services Panel notification"
    assert fields["tender_code"] == "DPC038672"
    assert fields["agency"] == "Procurement SA"
    assert fields["tender_type"] == "Request for Tender"
    assert fields["tender_status"] == "Open"
    assert fields["Contract Number"] == "SP-1323a"
    assert fields["Category"] == "Information Technology Broadcasting and Telecommunications"
    assert fields["Region(s)"] == "South Australia"
    assert fields["closing_date"] == "4:20 pm 16 September 2030"
    assert fields["contact_name"] == "Andrew Heitmann"
    assert fields["description"]


def test_the_hidden_contact_email_is_decoded():
    fields, _ = scraper.parse_detail(_signed_in_details())
    assert fields["contact_email"] == "DTFSPCategory2@sa.gov.au"


def test_cloudflare_email_decoding():
    key = 0x5A
    encoded = f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in "a@b.com")
    assert scraper.decode_cf_email(encoded) == "a@b.com"
    assert scraper.decode_cf_email("zz") is None
    assert scraper.decode_cf_email("") is None


def test_site_structure_change_when_field_section_missing():
    fields, code = scraper.parse_detail(_structure_changed())
    assert code == common.SITE_STRUCTURE_CHANGE
    assert fields == {}


def test_a_page_without_a_tender_code_is_a_structure_change():
    html = _signed_in_details().replace("Tender Code", "Tender Reference")
    assert scraper.parse_detail(html)[1] == common.SITE_STRUCTURE_CHANGE


def test_the_category_is_not_written_twice(tmp_path):
    entry = dict(ENTRY, category="Information Technology Broadcasting and Telecommunications")
    _, tender = scraper.scrape_opportunity(
        FakeSession(detail_html=_no_documents_details()), entry, str(tmp_path))
    text = open(os.path.join(tender["folder"], "__tender__DPC038672.txt")).read()
    head = text.split("FULL PAGE TEXT")[0]
    assert head.lower().count("category: information technology") == 1


def test_a_listing_with_several_categories_is_kept_when_it_says_more(tmp_path):
    entry = dict(ENTRY, category="A - 50%; B - 50%")
    _, tender = scraper.scrape_opportunity(
        FakeSession(detail_html=_no_documents_details()), entry, str(tmp_path))
    text = open(os.path.join(tender["folder"], "__tender__DPC038672.txt")).read()
    assert "listing_categories: A - 50%; B - 50%" in text


def test_documents_are_listed_with_version_and_size():
    docs = scraper.parse_documents(_signed_in_details())
    assert len(docs) == 4
    assert docs[0]["file_name"].startswith("Guide to Becoming an eProjects")
    assert docs[0]["version"].startswith("Version 1")
    assert docs[0]["size"] == "271 KB"


def test_signed_in_page_offers_the_download_form():
    html = _signed_in_details()
    assert scraper.find_download_docs_url(html) == \
        "https://www.tenders.sa.gov.au/secure/tender/downloadSpecDocs?tenderId=47223"
    assert not scraper.documents_require_login(html)


def test_anonymous_page_has_no_download_link():
    html = _anonymous_details()
    assert scraper.find_download_docs_url(html) is None
    assert scraper.documents_require_login(html)


def test_signed_in_and_signed_out_pages_are_told_apart():
    assert scraper.is_signed_in(_signed_in_details())
    assert scraper.is_signed_in(_read("sa_tenders_login_success.html"))
    assert scraper.is_signed_in(_read("sa_tenders_download_attachments.html"))
    assert not scraper.is_signed_in(_read("sa_tenders_login.html"))
    assert not scraper.is_signed_in(_read("sa_tenders_logout.html"))
    assert not scraper.is_signed_in(_read("sa_tenders_wrong_login.html"))


def test_wrong_password_page_is_recognised():
    assert scraper.login_failed(_read("sa_tenders_wrong_login.html"))
    assert not scraper.login_failed(_read("sa_tenders_login.html"))
    assert not scraper.login_failed(_read("sa_tenders_login_success.html"))


def test_the_document_form_is_read_from_the_real_capture():
    html = _read("sa_tenders_download_attachments.html")
    assert scraper.parse_download_form(html) == {"count": 4, "unchecked": 0}
    unticked = html.replace(' checked aria-label="Select Document"', ' aria-label="Select Document"')
    assert scraper.parse_download_form(unticked) == {"count": 4, "unchecked": 4}
    assert scraper.parse_download_form(_signed_in_details()) is None


# ---------------------------------------------------------------------------
# Full tender scrape
# ---------------------------------------------------------------------------

def test_anonymous_scrape_saves_page_text_and_reports_documents_gated(tmp_path):
    code, tender = scraper.scrape_opportunity(
        FakeSession(detail_html=_anonymous_details()), ENTRY, str(tmp_path))

    assert code == common.TENDER_PARTIAL
    assert tender["tender_id"] == "DPC038672"
    assert tender["documents_gated"] is True
    assert tender["attachments"] == []
    assert os.listdir(tender["folder"]) == ["__tender__DPC038672.txt"]

    text = open(os.path.join(tender["folder"], "__tender__DPC038672.txt")).read()
    assert "SA TENDERS: eProjects Panel" in text
    assert "tender_status: Open" in text
    assert "SPECIFICATION DOCUMENTS:" in text
    assert "Guide to Becoming an eProjects" in text


def test_the_page_text_carries_the_full_page_text_block(tmp_path):
    _, tender = scraper.scrape_opportunity(
        FakeSession(detail_html=_no_documents_details()), ENTRY, str(tmp_path))
    text = open(os.path.join(tender["folder"], "__tender__DPC038672.txt")).read()
    assert common.FULL_PAGE_HEADER in text
    assert "Contract Number" in text and "SP-1323a" in text


def test_signed_in_scrape_unpacks_the_document_zip(tmp_path):
    session = FakeSession(zip_bytes=_readable_zip())
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))

    assert code == common.SITE_SUCCESS
    assert [a["file_name"] for a in tender["attachments"]] == ["Guide.pdf"]
    saved = sorted(os.listdir(tender["folder"]))
    assert "documents.zip" not in saved
    assert "Guide.pdf.txt" in saved


def test_an_unreadable_document_makes_the_tender_partial(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Template.xlsx", b"not really a spreadsheet")
    session = FakeSession(zip_bytes=buf.getvalue())
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert [a["file_name"] for a in tender["attachments"]] == ["Template.xlsx"]


def test_a_failed_download_gives_tender_partial_not_a_crash(tmp_path):
    session = FakeSession(zip_bytes=None)
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert os.listdir(tender["folder"]) == ["__tender__DPC038672.txt"]


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


def test_page_text_records_the_source_url(tmp_path):
    _, tender = scraper.scrape_opportunity(
        FakeSession(detail_html=_no_documents_details()), ENTRY, str(tmp_path))
    assert common.read_source_url(tender["folder"]) == DETAIL_URL


def test_a_cloudflare_block_during_a_download_stops_the_run(tmp_path):
    class Blocking(FakeSession):
        def download_documents(self, url, folder):
            raise scraper.BotBlockedError(url)

    with pytest.raises(scraper.BotBlockedError):
        scraper.scrape_opportunity(Blocking(), ENTRY, str(tmp_path))


# ---------------------------------------------------------------------------
# Site-level results
# ---------------------------------------------------------------------------

def test_run_respects_limit(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession(detail_html=_no_documents_details()))
    code, tenders = scraper._scrape_site(limit=2, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert len(tenders) == 2


def test_run_scraper_returns_a_scrape_result(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession(detail_html=_no_documents_details()))
    result = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert isinstance(result, common.ScrapeResult)
    assert tuple(result) == (common.SITE_SUCCESS, "sa_tenders", 1)


def test_run_without_credentials_is_partial_when_documents_are_gated(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession(detail_html=_anonymous_details()))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert tenders and tenders[0]["documents_gated"]


def test_failed_login_is_reported_but_page_text_is_still_scraped(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    session = FakeSession(detail_html=_no_documents_details(), login_ok=False)
    _patch_session(monkeypatch, session)
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_LOGIN_FAILED
    assert len(tenders) == 1
    assert session.logins == 1


def test_signed_in_run_downloads_the_documents(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    session = FakeSession(zip_bytes=_readable_zip())
    _patch_session(monkeypatch, session)
    code, tenders = scraper._scrape_site(limit=2, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert all(t["attachments"] for t in tenders)


def test_cloudflare_block_is_site_bot_blocked(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession(blocked=True))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_BOT_BLOCKED
    assert tenders == []


def test_empty_listing_is_a_structure_change(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession(list_html=_read("sa_tenders_public_list.html")))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE


def test_browser_that_will_not_start_is_total_failure(tmp_path, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("no chrome")
    monkeypatch.setattr(scraper, "BrowserSession", boom)
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_TOTAL_FAILURE


@pytest.mark.parametrize("code", [common.SITE_LOGIN_FAILED, common.SITE_STRUCTURE_CHANGE])
def test_login_failures_and_structure_changes_are_never_retried(monkeypatch, code):
    calls = []
    monkeypatch.setattr(scraper, "_scrape_site", lambda *a, **k: calls.append(1) or (code, []))
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    assert scraper._scrape_with_retries()[0] == code
    assert len(calls) == 1


def test_a_cloudflare_block_is_retried_then_reported(monkeypatch):
    calls = []
    monkeypatch.setattr(scraper, "_scrape_site",
                        lambda *a, **k: calls.append(1) or (common.SITE_BOT_BLOCKED, []))
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    code, tenders = scraper._scrape_with_retries()
    assert code == common.SITE_BOT_BLOCKED
    assert len(calls) == scraper.BROWSER_ATTEMPTS


def test_a_block_after_some_tenders_is_kept_when_resuming(tmp_path, monkeypatch):
    class BlockedAfterOne(FakeSession):
        def get(self, url, wait_selector=None):
            if "/tender/view" in url and len(self.visited) >= 2:
                raise scraper.BotBlockedError(url)
            return super().get(url, wait_selector)

    _patch_session(monkeypatch, BlockedAfterOne(detail_html=_no_documents_details()))
    code, tenders = scraper._scrape_site(limit=3, output_dir=str(tmp_path), resume=True)
    assert len(tenders) == 1
    assert code == common.SITE_SUCCESS


def test_tenders_already_stored_go_last_when_resuming():
    entries = scraper.parse_listing(_list_page())
    known = {entries[0]["url"]}
    ordered, new = scraper.order_for_resume(entries, known)
    assert new == 14
    assert ordered[-1]["url"] == entries[0]["url"]


class LapsingSession(FakeSession):
    """Signed in until `lapse_after` detail pages were served; then the page
    comes back without the download link until login() is called again."""

    def __init__(self, lapse_after, relogin_works=True, **kwargs):
        super().__init__(**kwargs)
        self.lapse_after = lapse_after
        self.relogin_works = relogin_works
        self.served = 0
        self.lapsed = False

    def get(self, url, wait_selector=None):
        if "/tender/search" in url:
            return self.list_html
        if self.served >= self.lapse_after:
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


def test_a_lapsed_session_signs_in_again_and_carries_on(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    session = LapsingSession(lapse_after=1, zip_bytes=_readable_zip())
    _patch_session(monkeypatch, session)
    code, tenders = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert session.logins >= 2


def test_a_failed_relogin_is_not_retried_forever(tmp_path, monkeypatch):
    _set_credentials(monkeypatch)
    session = LapsingSession(lapse_after=0, relogin_works=False, zip_bytes=_readable_zip())
    _patch_session(monkeypatch, session)
    code, tenders = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert session.logins <= 1 + scraper.MAX_RELOGINS


# ---------------------------------------------------------------------------
# The browser's own steps, with SeleniumBase stood in for
# ---------------------------------------------------------------------------

class _FakeSB:
    def __init__(self, password_ok=True, page=None):
        self.password_ok = password_ok
        self.submitted = False
        self.typed = {}
        self.scripts = []
        self.page = page

    def uc_open_with_reconnect(self, url, reconnect_time=None):
        pass

    def wait_for_element(self, selector, timeout=None):
        pass

    def get_title(self):
        return "SA Tenders"

    def get_current_url(self):
        return scraper.LOGIN_URL

    def get_page_source(self):
        if self.page is not None:
            return self.page
        if not self.submitted:
            return _read("sa_tenders_login.html")
        return (_read("sa_tenders_login_success.html") if self.password_ok
                else _read("sa_tenders_wrong_login.html"))

    def type(self, selector, text):
        self.typed[selector] = text

    def click(self, selector):
        self.submitted = True

    def is_element_present(self, selector):
        return self.submitted and self.password_ok and selector == scraper.SIGNED_IN_SELECTOR

    def execute_script(self, script):
        self.scripts.append(script)


def test_browser_login_succeeds_when_the_logout_link_appears(monkeypatch):
    _set_credentials(monkeypatch)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    session = scraper.BrowserSession()
    session.sb = _FakeSB(password_ok=True)
    assert session.login() is True
    assert session.sb.typed == {"#supplierUsername": "someone@example.com",
                                "#supplierPassword": "not-a-real-password"}


def test_browser_login_fails_on_the_sites_wrong_password_message(monkeypatch):
    _set_credentials(monkeypatch)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    session = scraper.BrowserSession()
    session.sb = _FakeSB(password_ok=False)
    assert session.login() is False


def test_browser_get_gives_up_on_the_first_challenge():
    session = scraper.BrowserSession()
    session.sb = _FakeSB(page="<html><body>Sorry, you have been blocked</body></html>")
    with pytest.raises(scraper.BotBlockedError):
        session.get(scraper.LIST_URL)


class _PagerSB(_FakeSB):
    """
    The site's pager as the real page behaves: the form sits inside #resultDiv,
    so emptying #resultDiv first removes it and the pager call then fails with
    the error seen on the live site. The answer shows up after `delay` reads.
    """

    def __init__(self, first, second, delay=0):
        super().__init__(page=first)
        self.first, self.second, self.delay = first, second, delay
        self.form_present = True
        self.requested = False
        self.reads_since_request = 0

    def execute_script(self, script):
        super().execute_script(script)
        if "empty()" in script:
            self.form_present = False
        if "groupedSearchForm" in script:
            if not self.form_present:
                raise RuntimeError("javascript error: Cannot read properties of undefined (reading 'page')")
            self.requested = True

    def get_page_source(self):
        if self.requested:
            self.reads_since_request += 1
            if self.reads_since_request > self.delay:
                return self.second
        return self.first


def _two_pages():
    first = _list_page(_page((1, "A", "a"), (2, "B", "b"), total=4))
    second = _list_page(_page((3, "C", "c"), (4, "D", "d"), total=4, first=3))
    return first, second


def test_opening_a_page_calls_the_sites_own_pager_without_emptying_the_results(monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    first, second = _two_pages()
    session = scraper.BrowserSession()
    session.sb = _PagerSB(first, second)
    html = session.open_page(2)
    assert session.sb.scripts == ["document.forms.groupedSearchForm.page.value=2;"
                                  "changeGroupedTenderTablePageAjax();"]
    assert [e["tender_code"] for e in scraper.parse_listing(html)] == ["C", "D"]


def test_opening_a_page_waits_until_the_new_rows_have_arrived(monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    first, second = _two_pages()
    session = scraper.BrowserSession()
    session.sb = _PagerSB(first, second, delay=3)
    html = session.open_page(2)
    assert [e["tender_code"] for e in scraper.parse_listing(html)] == ["C", "D"]
    assert session.sb.reads_since_request == 4  # three stale reads, then the new page


def test_a_page_that_never_arrives_comes_back_unchanged_and_does_not_hang(monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    monkeypatch.setattr(scraper, "WAIT_TIMEOUT", 0)
    first, second = _two_pages()
    session = scraper.BrowserSession()
    session.sb = _PagerSB(first, second, delay=10_000)
    html = session.open_page(2)
    assert [e["tender_code"] for e in scraper.parse_listing(html)] == ["A", "B"]


def test_a_page_number_that_is_not_a_number_is_refused():
    session = scraper.BrowserSession()
    session.sb = _FakeSB()
    with pytest.raises(ValueError):
        session.open_page("2;alert(1)")


def test_the_download_form_page_without_a_form_is_reported_not_clicked(tmp_path):
    session = scraper.BrowserSession()
    session.sb = _FakeSB(page=_signed_in_details())
    session._downloads_dir = str(tmp_path / "dl")
    with pytest.raises(common.StructureChangedError):
        session.download_documents("https://x/secure/tender/downloadSpecDocs?tenderId=1", str(tmp_path))