"""
Tests for the QTenders scraper, same discipline as
error_scrapers/buy_nsw/test_cases/test_buynsw.py.

qld_search_page.json is a live response from POST /api/search/tenders (page
1 of 5), cut to its first 3 tenders. qld_vendorpanel_preview.html is the live
VendorPanel preview for VP527073, the second of them, with scripts/styles
removed. The structure-changed preview is built at runtime by renaming every
"opportunityPreviewMinHeading" class.
"""

import copy
import json
import os

import fitz
import httpx
import pytest

from error_scrapers import common
from error_scrapers.qld_qtenders import scraper

FIXTURES = os.path.dirname(__file__)


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def _search_page() -> dict:
    return json.loads(_read_fixture("qld_search_page.json"))


def _tender() -> dict:
    """The search record the preview fixture belongs to."""
    return next(t for t in _search_page()["tenders"] if t["vpReference"] == "VP527073")


def _structure_changed() -> str:
    return _read_fixture("qld_vendorpanel_preview.html").replace(
        "opportunityPreviewMinHeading", "opportunityPreviewMinHeading-CHANGED"
    )


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _signed_in_preview() -> str:
    """The preview as a signed-in supplier would see it: every document linked."""
    links = "".join(
        f'<a href="/DownloadTenderDocument.aspx?id={i}" title="Part {i}.pdf">Part {i}.pdf</a>'
        for i in range(1, 8)
    )
    return _read_fixture("qld_vendorpanel_preview.html").replace("</body>", links + "</body>")


def _portal(preview_html=None, preview_status=200, pages=None):
    """
    A fake QTenders API + VendorPanel. `pages` maps page number -> search
    response; by default page 1 is the fixture and every other page is empty.
    """
    preview_html = preview_html or _read_fixture("qld_vendorpanel_preview.html")
    pages = pages or {1: _search_page()}
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/search/tenders":
            body = json.loads(request.content)
            requested.append(body["pageNumber"])
            empty = {"tenders": [], "totalPages": len(pages)}
            return httpx.Response(200, json=pages.get(body["pageNumber"], empty))
        if request.url.path == "/PublicTenderPreviewPop.aspx":
            return httpx.Response(preview_status, text=preview_html)
        if request.url.path == "/DownloadTenderDocument.aspx":
            return httpx.Response(200, content=_pdf_bytes("Scope of works"),
                                  headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    transport.requested_pages = requested
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


# ---------------------------------------------------------------------------
# Search API
# ---------------------------------------------------------------------------

def test_search_page_yields_tenders_and_page_count():
    tenders, total_pages = scraper.parse_search_page(_search_page())
    assert len(tenders) == 3
    assert total_pages == 5
    assert all(t["vpReference"].startswith("VP") for t in tenders)


def test_search_response_without_tenders_is_a_structure_change():
    with pytest.raises(common.StructureChangedError):
        scraper.parse_search_page({"results": []})


def test_search_body_asks_for_open_tenders_only():
    body = scraper.search_body(3)
    assert body["tenderStatusIds"] == ["1"]
    assert body["pageNumber"] == 3


def test_collect_walks_pages_until_the_reported_count():
    page_one = _search_page()
    page_two = copy.deepcopy(page_one)
    for tender in page_two["tenders"]:
        tender["vpReference"] += "-2"
    page_one["totalPages"] = page_two["totalPages"] = 2

    transport = _portal(pages={1: page_one, 2: page_two})
    with _client(transport) as client:
        tenders = scraper.collect_all_tenders(client)
    assert len(tenders) == 6
    assert transport.requested_pages == [1, 2]


def test_collect_respects_limit():
    with _client(_portal()) as client:
        assert len(scraper.collect_all_tenders(client, limit=3)) == 3


# ---------------------------------------------------------------------------
# Preview page parsing
# ---------------------------------------------------------------------------

def test_success_parses_vendorpanel_preview():
    fields, code = scraper.parse_detail(_read_fixture("qld_vendorpanel_preview.html"))
    assert code == common.SITE_SUCCESS
    lookup = {(section, label): value for section, label, value in fields["sections"]}
    assert lookup[("Tender Details", "VP Reference #")] == "VP527073"
    assert lookup[("Buyer Details", "Business Name")] == "University of Southern Queensland"
    assert "Supplier query cut-off" in {label for _, label, _ in fields["sections"]}
    assert fields["documents_advertised"] == 7
    # the public preview names no documents
    assert fields["documents"] == []


def test_site_structure_change_when_label_cells_missing():
    fields, code = scraper.parse_detail(_structure_changed())
    assert code == common.SITE_STRUCTURE_CHANGE
    assert fields == {}


def test_details_html_becomes_plain_text():
    text = scraper.html_to_text("<p>First &amp; foremost</p><ul><li>item</li></ul>")
    assert text == "First & foremost\nitem"


# ---------------------------------------------------------------------------
# Full tender scrape
# ---------------------------------------------------------------------------

def test_public_preview_saves_page_text_and_reports_documents_gated(tmp_path):
    with _client(_portal()) as client:
        code, result = scraper.scrape_opportunity(client, _tender(), str(tmp_path))

    assert code == common.TENDER_PARTIAL
    assert result["tender_id"] == "VP527073"
    assert result["documents_gated"] is True
    assert result["attachments"] == []
    assert os.listdir(result["folder"]) == ["__tender__VP527073.txt"]

    text = open(os.path.join(result["folder"], "__tender__VP527073.txt")).read()
    assert "UniSQ Servicing of Electrical, Mechanical and Electro-Mechanical Infrastructure" in text
    assert "Buyers Reference: UniSQ2026116242" in text
    assert "BUYER DETAILS:" in text
    assert "<p>" not in text


def test_signed_in_preview_downloads_every_document(tmp_path):
    with _client(_portal(_signed_in_preview())) as client:
        code, result = scraper.scrape_opportunity(client, _tender(), str(tmp_path))

    assert code == common.SITE_SUCCESS
    assert result["documents_gated"] is False
    assert len(result["attachments"]) == 7
    assert os.path.exists(os.path.join(result["folder"], "Part 1.pdf.txt"))


def test_unreachable_preview_still_saves_the_search_record(tmp_path):
    with _client(_portal(preview_status=503)) as client:
        code, result = scraper.scrape_opportunity(client, _tender(), str(tmp_path))
    assert code == common.TENDER_PARTIAL
    text = open(os.path.join(result["folder"], "__tender__VP527073.txt")).read()
    assert "VP Reference: VP527073" in text


def test_structure_change_on_preview_returns_no_tender(tmp_path):
    with _client(_portal(_structure_changed())) as client:
        code, result = scraper.scrape_opportunity(client, _tender(), str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE
    assert result == {}
    assert os.listdir(tmp_path) == []


# ---------------------------------------------------------------------------
# Site-level results
# ---------------------------------------------------------------------------

def test_run_scraper_respects_limit_and_reports_partial(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal())
    code, tenders = scraper.run_scraper(limit=2, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert len(tenders) == 2


def test_run_is_structure_change_when_the_api_changes(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(
        lambda request: httpx.Response(200, json={"items": []})
    ))
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE
    assert tenders == []


def test_run_is_structure_change_when_the_api_stops_returning_json(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>maintenance</html>")
    ))
    code, _ = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE


def test_site_total_failure_on_unreachable_url(monkeypatch):
    def exploding_post(*args, **kwargs):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx.Client, "post", exploding_post)
    code, tenders = scraper.run_scraper()
    assert code == common.SITE_TOTAL_FAILURE
    assert tenders == []


def test_rate_limited_on_429(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(429)))
    code, _ = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_RATE_LIMITED
