"""
Tests for the QTenders scraper, same discipline as
error_scrapers/buy_nsw/test_cases/test_buynsw.py.

qld_search_page.json is a live response from POST /api/search/tenders (page
1 of 5), cut to its first 3 tenders. qld_vendorpanel_preview.html is the live
VendorPanel preview for VP527073, the second of them, with scripts/styles
removed. The structure-changed preview is built at runtime by renaming every
"opportunityPreviewMinHeading" class.
"""

import contextlib
import copy
import io
import json
import os
import zipfile

import fitz
import httpx
import pytest

from error_scrapers import common
from error_scrapers.qld_qtenders import scraper

FIXTURES = os.path.dirname(__file__)


# Small captured pages embedded so the tests need no extra fixture files; a real
# file in this folder with the same name takes precedence.
_EMBEDDED = {
    'qld_vendorpanel_preview.html': (
        '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'
    ),
    'qld_row_unfollowed.html': (
        'eNrdWG1v2zYQ/t5fcdCHOQGi2E6XFk0sF026YQHWxci8AAMKDLR4sthQpEZSdrxfP1KUY7eVZC11UmD8IEvU8V6eO1'
        'LPeWQUMBoFpyev37z5MRiPDIWYE62jgJrQrHIMRZGhYnEwPh31DXUS4xFli7WYzFFMUVBUwfgFfDG2BU0p9C42TIob'
        'uayRrl/xG8mwQdiNq+n05PTlKUyISZdkBURQmCBFbRQjAi4Uo3OEnwrKJVwqxLt6s31rt6tHPgbd4tSIQKowiYJPZE'
        'F0rFhuzhaS0YPB4XkAUsScxXdRQD6R+yUTEU1NxjNJCT92cB70vJ1c4YLh8i+fnN4R9NwC99v/gNkMle7fWkGpJl7w'
        'Os+lMoVgZnVMdH7/Vm4mrmi0UVPJu9sloyaNhsPBIL8/SpHNUxO9Kh8UavYPRsMj67/knIm5vY9RGFTRsHd4DgpNoQ'
        'QkhGu0UVUQcXE3M2LGi7aklRix9ZKEWCWhRqLiNABi8xamjFIUFnDl9Iz6bAytyqqImhPSJy3Z2s6wnM85XkphCBO1'
        'Nb09Wl/Wq/6VzJDvULseP1vc5XK3kebi/aIoK09YLMVFYYwUQVWn/SQ3x+Sevp1Hg2qENZf1+IFk+bnKo45xbKeau1'
        'x7KEKZJI357hB1W0pb9nP9q6bpr3b/haSrzofXe7R1xFuPiqY1zWfkw1Kd2xOubm3XIrvSukAKs9VZu6G+s9TBmd0W'
        'fy+ETu3OgktJtLHXQsSMf5P1HeX/XQG+ncANJqhQxPhsGN9O/FH/lKi+2CPU3wZzB3x3xrob14pmPMrEY6D87pV7bY'
        'kI0uc7F4glE9Tyt8FLuI6NtOwGTgYnr+zz2WAA7z78b4+ISy61JVfPhvS0QO2APnn9OdDDwdMC3ZFiJyXnQep7Cs9S'
        'un4/CUdlblAX3GhP0i+MeOByMPPKQuVYLjgSFPqpcKlInju2V8eRYHP7vuDc8fdMFhrlwjLhYMryg54namBSpsE7Dk'
        'Zagvy3xdpAJhUCE4lUGXFO9XwT4JUUJgr+EE6Lm23uGsbbJEo7ElUqZ47DNVAox5D2GhCJY9QaqIxtTyiMfkQgNXEk'
        'zFJBws0TxrEJgcql4JJQH9qNz9CeEkKUkkvLbcOZtD5l/zmeJh++DusXRhH8aQOJkhm45gdC+FMWENvzqHwsSToUIt'
        '0Ia/uG2NARPva8Xx97az2u2QebC9vf9Q4f+oK06gts1FUDWaol5e6KnB81QOAKQ21n0o4QwB5qkwnKYmIQbPjMNqnC'
        'Oeh3oc6loM05/sx/VVYkJ/Pn21WxwtLvylONeyrHcl9pNhfu04rt8TyG7LX1Ueur+7Oob9T4X4TothI='
    ),
    'qld_row_followed.html': (
        'eNrNWFtP4zgUfudXWH0YQKJXYJiBpojCroS0QKdTWO3Tyo1PGw+OnfGlpYgfv8dJCi20mTACLXlIE/s4Puf7zs1tW0'
        '04Cyr7rYOvX/cqnbZlJBTUmKDCbNXOEqhKF4PmYaWz165b5iU6bcYnc7GREkJNgQ1AMtCVzsbipE0HT0LLleyr6crZ'
        'SxoDTpA11/lg0Nrf3Sc9aqMpnREqGekBA2M1p5J0NWdjIH84JhQ51QC3K7/UruO+63UzXjM6nxHydmjlUDiokEjDKK'
        'j8oBNqQs0TezhRnG01to8qRMlQ8PA2qNAf9G7KZcAiG4tYMSpqKgG5tZltkWiYcJj+m2G8uUM2/QL/W7+AeAja1G9Q'
        'UOleJniVJEpbJ7md1ahJ7o7V08A5C54+k8v7xylnNgqazUYjuduJgI8jG3xOXzQYfg9Bcwf1R6q4HONzCNKCDpqb20'
        'dEg3VakhEVBo48DpxQhLYaccZAIk7a4zBnm6Jg1QDVYYTeUucdspY5f+UqrpVp1+kzVtR4LOBUSUu5fOlP6exfdAii'
        'wGWeX9cyc9JSC+ae8ugNPFSy66xVcu4NdTdKbI3esWM1Dhr5VV1xm1+faJwcvUJUJ8GviRCeiQyQKuqWkrGR4ZmbsP'
        'zzwvW7is1WRuQZIPjCFM3lwWwSDMFV82UpOjfGASPD2eFGMSl+p3zDX3/1u5MmQvchp4oai3cnQy7K7bAOrTc2/KZH'
        '+jACDTKEN7X9ppcliA9jbQkzX2FiXg42PgqPV5jogb2t91JMyAxLXWOXXIVWYYUgrUbrM74fNhrk5OJDOfKpUAaLyp'
        'siMHBgPACtg2UAmo3fAOAlDsstS5bbn2c7KkDbPhgnrMl6hK6Vj2WJDLNFVe0rLfElopoNVaeaJokvXEUV5Df7Cap1'
        'zIv6iBNz24efiJ795m+odXEHgfJkpDSJlQbCsUzqmPpVb9pSoGGxcgaUs0HlWg54srW9MDrBVRU/uPmPciREd9SZCS'
        '+0wiXERtyQ3EbiEmIVGUROZ+7SWu0uNdQJ+Sgup8aX0585alk19cX0XVik1hZ2g8tN4Im1NIxixNcUs3mmQpeKEZou'
        'wcKK8KSI5ZA+8fplkda9/fek9SQMwRjS3CNsvYI5paWZGnFse6iw78rURCf3RUTxPzUeXnoquUgXpeQkdAzfdRg86+'
        '3P1FQKRdkCrz0a3qJwuuzh4WEVq2k3mBF28AU5St9z0nY9gxnt2acfwyL/8P9C9qMygGMzpFaOPcdMkSm30XL4+qOc'
        'oRPwAjPlNAlVnDjUgGzdc0yijGShv10j1waytSja659fDsrHNCZNNcVGuTpU6BJxscOQp8czJ8R6/3ldVoudz1b5MW'
        'QpIskQRj7NzfLch5ZgzsM4KGcczKBqcCQqbVZ34Vhbx2pmdXqSocGrTyk2GKQalQXiUlk+mpGumyHDNqI2NZpL33Z4'
        'XtElEyVZGWJ1mgIEHZc2+9HkeVgeMxX8ze+pZuawn+5sIHtPjVsVjeVNPdVALRCa22Sg9rrEZvhY+i4QFux76mM6qw'
        '5487v/e6Zudec/+UJjIQ=='
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
def _no_pause_no_credentials(monkeypatch):
    monkeypatch.setattr(scraper, "PAUSE_SECONDS", 0)
    monkeypatch.delenv("QLD_USERNAME", raising=False)
    monkeypatch.delenv("QLD_PASSWORD", raising=False)


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
    code, tenders = scraper._scrape_site(limit=2, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert len(tenders) == 2


def test_run_is_structure_change_when_the_api_changes(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(
        lambda request: httpx.Response(200, json={"items": []})
    ))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE
    assert tenders == []


def test_run_is_structure_change_when_the_api_stops_returning_json(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>maintenance</html>")
    ))
    code, _ = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE


def test_site_total_failure_on_unreachable_url(monkeypatch):
    def exploding_post(*args, **kwargs):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx.Client, "post", exploding_post)
    code, tenders = scraper._scrape_site()
    assert code == common.SITE_TOTAL_FAILURE
    assert tenders == []


def test_rate_limited_on_429(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(429)))
    code, _ = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_RATE_LIMITED


def test_403_is_bot_blocked_and_stops_the_run(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(403)))
    code, tenders = scraper._scrape_site(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_BOT_BLOCKED
    assert tenders == []


def test_429_on_a_preview_page_stops_the_run(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal(preview_status=429))
    code, tenders = scraper._scrape_site(limit=2, output_dir=str(tmp_path))
    assert code == common.SITE_RATE_LIMITED


def test_run_scraper_returns_a_scrape_result(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal())
    result = scraper.run_scraper(limit=2, output_dir=str(tmp_path))
    assert isinstance(result, common.ScrapeResult)
    assert (result.code, result.site, result.count) == (common.TENDER_PARTIAL, "qld_qtenders", 2)


def test_page_text_records_the_source_url(tmp_path):
    with _client(_portal()) as client:
        code, result = scraper.scrape_opportunity(client, _tender(), str(tmp_path))
    assert common.read_source_url(result["folder"]) == _tender()["tenderPreviewUrl"]


# ---------------------------------------------------------------------------
# VendorPanel documents: follow, then download the package
# ---------------------------------------------------------------------------

def _package_zip(documents=7) -> bytes:
    """What VendorPanel's package holds: a summary PDF, then the attachments."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("VP527073/Request Summary Report.pdf", _pdf_bytes("The buyer has attached 7 documents"))
        for i in range(1, documents + 1):
            zf.writestr(f"VP527073/RequestDocs/Part {i}.pdf", _pdf_bytes(f"Part {i}"))
    return buf.getvalue()


class FakeVendorPanel:
    """
    Stands in for VendorPanelSession. `followed` is the set of VendorPanel numbers
    that already show Unfollow; `follow_works=False` makes the toggle do nothing.
    """

    def __init__(self, followed=(), known=None, follow_works=True, package=None,
                 lapse_on_first=0, login_ok=True):
        self.followed = set(followed)
        self.known = known  # None: every number is on the list
        self.follow_works = follow_works
        self.package = _package_zip() if package is None else package
        self.lapse_on_first = lapse_on_first
        self.login_ok = login_ok
        self.logins = 0
        self.follow_calls = []
        self.downloads = []

    def login(self):
        self.logins += 1
        return self.login_ok

    def find_row(self, number):
        if self.lapse_on_first:
            self.lapse_on_first -= 1
            raise scraper.SessionLapsedError("VendorPanel is showing its sign-in page")
        if self.known is not None and number not in self.known:
            return None
        return {"followed": number in self.followed, "can_follow": True,
                "can_download": number in self.followed, "documents": 7}

    def follow(self, number):
        self.follow_calls.append(number)
        if self.follow_works:
            self.followed.add(number)
        return self.follow_works

    def download_package(self, number, folder):
        self.downloads.append(number)
        path = os.path.join(folder, "package.zip")
        with open(path, "wb") as f:
            f.write(self.package)
        return path


def _scrape_with(vendorpanel, tmp_path):
    with _client(_portal()) as client:
        return scraper.scrape_opportunity(client, _tender(), str(tmp_path), vendorpanel)


def test_vp_numbers_come_from_the_reference():
    assert scraper.vp_number("VP528160") == "528160"
    assert scraper.vp_number("vp1") == "1"
    assert scraper.vp_number("PQS26-003") is None
    assert scraper.vp_number(None) is None


def test_an_unfollowed_row_offers_follow_and_nothing_to_download():
    row = scraper.parse_row(_read_fixture("qld_row_unfollowed.html"), "527994")
    assert row == {"followed": False, "can_follow": True, "can_download": False, "documents": None}


def test_a_followed_row_shows_its_document_count_and_download():
    row = scraper.parse_row(_read_fixture("qld_row_followed.html"), "527994")
    assert row == {"followed": True, "can_follow": False, "can_download": True, "documents": 14}


def test_a_row_that_is_not_on_the_page_is_none():
    assert scraper.parse_row(_read_fixture("qld_row_followed.html"), "999") is None


def test_the_follow_selector_can_never_match_the_hide_or_unfollow_links():
    from bs4 import BeautifulSoup
    unfollowed = BeautifulSoup(_read_fixture("qld_row_unfollowed.html"), "html.parser")
    assert "hpt.axd" in str(unfollowed)  # the row really does carry a Hide link
    hits = unfollowed.select(scraper.FOLLOW_LINK)
    assert len(hits) == 1 and "/fpt.axd" in hits[0]["href"]
    followed = BeautifulSoup(_read_fixture("qld_row_followed.html"), "html.parser")
    assert followed.select(scraper.FOLLOW_LINK) == []


def test_signed_in_pages_are_told_apart_by_the_logout_link():
    assert scraper.is_signed_in("<a href='/logout.axd' class='userInfoLinks'>Logout</a>")
    assert not scraper.is_signed_in(_read_fixture("qld_login.html"))


def test_an_unfollowed_tender_is_followed_then_downloaded(tmp_path):
    vp = FakeVendorPanel()
    code, result = _scrape_with(vp, tmp_path)
    assert code == common.SITE_SUCCESS
    assert vp.follow_calls == ["527073"] and vp.downloads == ["527073"]
    assert result["followed"] is True and result["documents_gated"] is False
    assert len(result["attachments"]) == 8  # seven documents plus the summary PDF
    assert not os.path.exists(os.path.join(result["folder"], "package.zip"))
    assert os.path.exists(os.path.join(result["folder"], "Part 1.pdf.txt"))


def test_an_already_followed_tender_is_not_followed_again(tmp_path):
    vp = FakeVendorPanel(followed={"527073"})
    code, result = _scrape_with(vp, tmp_path)
    assert code == common.SITE_SUCCESS
    assert vp.follow_calls == [] and result["followed"] is True


def test_a_follow_that_does_not_take_is_partial_and_downloads_nothing(tmp_path):
    vp = FakeVendorPanel(follow_works=False)
    code, result = _scrape_with(vp, tmp_path)
    assert code == common.TENDER_PARTIAL
    assert result["followed"] is False and result["documents_gated"] is True
    assert vp.downloads == []


def test_a_tender_missing_from_the_list_is_partial(tmp_path):
    code, result = _scrape_with(FakeVendorPanel(known={"1"}), tmp_path)
    assert code == common.TENDER_PARTIAL and result["followed"] is False


def test_a_package_with_fewer_files_than_advertised_is_partial(tmp_path):
    code, result = _scrape_with(FakeVendorPanel(package=_package_zip(documents=2)), tmp_path)
    assert code == common.TENDER_PARTIAL
    assert result["documents_gated"] is True and len(result["attachments"]) == 3


def test_a_lapsed_session_is_flagged_for_a_new_sign_in(tmp_path):
    code, result = _scrape_with(FakeVendorPanel(lapse_on_first=1), tmp_path)
    assert code == common.TENDER_PARTIAL and result["session_lapsed"] is True


def test_a_download_error_is_partial_not_a_crash(tmp_path):
    class Broken(FakeVendorPanel):
        def download_package(self, number, folder):
            raise TimeoutError("did not finish")

    code, result = _scrape_with(Broken(), tmp_path)
    assert code == common.TENDER_PARTIAL and result["followed"] is True


def _credentials(monkeypatch):
    monkeypatch.setenv("QLD_USERNAME", "someone@example.com")
    monkeypatch.setenv("QLD_PASSWORD", "not-a-real-password")


def _patch_vendorpanel(monkeypatch, vp):
    monkeypatch.setattr(scraper, "VendorPanelSession", lambda **kwargs: contextlib.nullcontext(vp))


def test_a_signed_in_run_follows_and_downloads_every_tender(tmp_path, monkeypatch, caplog):
    _credentials(monkeypatch)
    vp = FakeVendorPanel()
    _patch_vendorpanel(monkeypatch, vp)
    _patch_client(monkeypatch, _portal())
    caplog.set_level("INFO")
    code, results = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert len(results) == 3 and all(len(r["attachments"]) == 8 for r in results)
    assert "follow: 3/3 tender(s) with documents are followed" in caplog.text


def test_a_follow_shortfall_is_reported_as_an_error(tmp_path, monkeypatch, caplog):
    _credentials(monkeypatch)
    _patch_vendorpanel(monkeypatch, FakeVendorPanel(follow_works=False))
    _patch_client(monkeypatch, _portal())
    code, results = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert "only 0/3 tender(s) with documents are followed" in caplog.text


def test_a_sign_in_that_does_not_take_is_login_failed_but_page_text_is_kept(tmp_path, monkeypatch):
    _credentials(monkeypatch)
    vp = FakeVendorPanel(login_ok=False)
    _patch_vendorpanel(monkeypatch, vp)
    _patch_client(monkeypatch, _portal())
    code, results = scraper._scrape_site(limit=2, output_dir=str(tmp_path))
    assert code == common.SITE_LOGIN_FAILED
    assert len(results) == 2 and all(r["attachments"] == [] for r in results)
    assert vp.logins == 1  # a failed sign-in is never retried
    assert vp.follow_calls == []  # and nothing is followed without a session


def test_a_browser_that_will_not_start_is_login_failed_not_a_crash(tmp_path, monkeypatch):
    _credentials(monkeypatch)

    def no_browser(**kwargs):
        raise RuntimeError("chrome not found")

    monkeypatch.setattr(scraper, "VendorPanelSession", no_browser)
    _patch_client(monkeypatch, _portal())
    code, results = scraper._scrape_site(limit=2, output_dir=str(tmp_path))
    assert code == common.SITE_LOGIN_FAILED
    assert len(results) == 2


def test_a_lapsed_session_signs_in_again_and_carries_on(tmp_path, monkeypatch):
    _credentials(monkeypatch)
    vp = FakeVendorPanel(lapse_on_first=1)
    _patch_vendorpanel(monkeypatch, vp)
    _patch_client(monkeypatch, _portal())
    code, results = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert vp.logins == 2  # the first sign-in, then one after the lapse


def test_relogins_are_capped(tmp_path, monkeypatch):
    _credentials(monkeypatch)
    vp = FakeVendorPanel(lapse_on_first=10_000)
    _patch_vendorpanel(monkeypatch, vp)
    _patch_client(monkeypatch, _portal())
    code, results = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert vp.logins == 1 + scraper.MAX_RELOGINS


def test_without_credentials_nothing_is_followed(tmp_path, monkeypatch):
    vp = FakeVendorPanel()
    _patch_vendorpanel(monkeypatch, vp)
    _patch_client(monkeypatch, _portal())
    code, results = scraper._scrape_site(limit=3, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert vp.logins == 0 and vp.follow_calls == []


# ---------------------------------------------------------------------------
# The browser session itself, against a fake SeleniumBase
# ---------------------------------------------------------------------------

class _FakeSB:
    """Records what the session does. `signed_in_after` is the number of submit clicks
    after which the Logout link appears (None: never)."""

    def __init__(self, downloads_dir=None, signed_in_after=2, rows=None, package_button=True):
        self.downloads_dir = downloads_dir
        self.signed_in_after = signed_in_after
        self.rows = rows if rows is not None else {}
        self.package_button = package_button
        self.clicks, self.typed, self.opened = [], [], []
        self.submits = 0
        self.page = "<html></html>"

    def open(self, url):
        self.opened.append(url)
        self.page = ("<a href='/logout.axd'>Logout</a>" + "".join(self.rows.values())
                     if self.signed_in_after == 0 else "<html></html>")

    def wait_for_element(self, selector, timeout=None):
        if selector == scraper.PACKAGE_BUTTON and not self.package_button:
            raise RuntimeError("not found")
        if selector.startswith("tr[id=") and not any(f'id="{n}"' in h for n, h in self.rows.items()):
            raise RuntimeError("no such row")

    def type(self, selector, text):
        self.typed.append((selector, text))

    def click(self, selector):
        self.clicks.append(selector)
        if selector == scraper.LOGIN_SUBMIT:
            self.submits += 1
        if selector == scraper.PACKAGE_BUTTON and self.downloads_dir:
            with open(os.path.join(self.downloads_dir, "VP527073.zip"), "wb") as f:
                f.write(_package_zip())

    def is_element_present(self, selector):
        return selector == scraper.SIGNED_IN_SELECTOR and self.signed_in_after is not None \
            and self.submits >= self.signed_in_after

    def get_page_source(self):
        return "<a href='/logout.axd'>Logout</a>" + "".join(self.rows.values()) \
            if self.signed_in_after == 0 or self.is_element_present(scraper.SIGNED_IN_SELECTOR) \
            else self.page

    def get_current_url(self):
        return "https://login.vendorpanel.com.au/account/Login"


def _session(sb):
    session = scraper.VendorPanelSession()
    session.sb = sb
    session._downloads_dir = sb.downloads_dir or ""
    return session


def test_browser_sign_in_is_two_steps_then_waits_for_the_logout_link(monkeypatch):
    _credentials(monkeypatch)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    sb = _FakeSB(signed_in_after=2)
    assert _session(sb).login() is True
    assert sb.typed == [(scraper.LOGIN_USER, "someone@example.com"),
                        (scraper.LOGIN_PASSWORD, "not-a-real-password")]
    assert sb.clicks == [scraper.LOGIN_SUBMIT, scraper.LOGIN_SUBMIT]  # Next, then sign in


def test_browser_sign_in_that_never_shows_the_logout_link_is_false(monkeypatch):
    _credentials(monkeypatch)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    assert _session(_FakeSB(signed_in_after=None)).login() is False


def test_browser_follow_clicks_only_the_follow_link_and_confirms_the_row(monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    sb = _FakeSB(signed_in_after=0, rows={"527994": _read_fixture("qld_row_unfollowed.html")})
    session = _session(sb)
    assert session.find_row("527994")["followed"] is False
    sb.rows["527994"] = _read_fixture("qld_row_followed.html")  # the site now says Unfollow
    assert session.follow("527994") is True
    assert sb.clicks == [f"tr[id='527994'] {scraper.FOLLOW_LINK}"]
    assert all("hpt" not in c for c in sb.clicks)


def test_browser_find_row_is_none_when_the_search_shows_nothing(monkeypatch):
    sb = _FakeSB(signed_in_after=0, rows={})
    assert _session(sb).find_row("999") is None


def test_browser_find_row_notices_a_signed_out_session():
    sb = _FakeSB(signed_in_after=None)
    with pytest.raises(scraper.SessionLapsedError):
        _session(sb).find_row("527994")


def test_browser_download_moves_the_zip_into_the_tender_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    downloads = tmp_path / "downloaded_files"
    downloads.mkdir()
    folder = tmp_path / "VP527073"
    folder.mkdir()
    sb = _FakeSB(downloads_dir=str(downloads))
    path = _session(sb).download_package("527073", str(folder))
    assert os.path.dirname(path) == str(folder) and os.path.exists(path)
    assert sb.opened[-1].endswith("VendorDownloadOpportunityPackage.aspx?opportunityId=527073")
    assert sb.clicks == [scraper.PACKAGE_BUTTON]


def test_browser_download_page_without_the_button_is_a_structure_change(tmp_path):
    sb = _FakeSB(downloads_dir=str(tmp_path), package_button=False, signed_in_after=0)
    with pytest.raises(common.StructureChangedError):
        _session(sb).download_package("527073", str(tmp_path))
    assert sb.clicks == []



# ---------------------------------------------------------------------------
# Agency name
# ---------------------------------------------------------------------------

def test_a_real_department_is_the_agency():
    assert scraper.agency_name({"departmentName": "Queensland Health", "businessName": "X"}) == "Queensland Health"


def test_an_unspecified_department_falls_back_to_the_business_name():
    tender = {"departmentName": "Unspecified", "businessName": "Queensland Health"}
    assert scraper.agency_name(tender) == "Queensland Health"
    assert "Agency: Queensland Health" in scraper.format_detail_text(tender, {})


def test_no_agency_at_all_is_none_and_omitted_from_the_text():
    assert scraper.agency_name({"departmentName": "Unspecified"}) is None
    assert "Agency:" not in scraper.format_detail_text({"title": "T", "vpReference": "VP1"}, {})