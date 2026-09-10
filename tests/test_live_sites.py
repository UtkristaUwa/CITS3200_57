"""
Live tests -- these hit the real tender portals.

Skipped unless you pass --live (or set RUN_LIVE_TESTS=1), so CI and everyday
`pytest` runs stay offline and deterministic:

    pytest --live tests/test_live_sites.py

They answer two questions the offline suite cannot: is the portal still up and
still shaped the way the scraper expects, and does a document download actually
work end to end against the real thing. When one fails, the scraper is
generally fine and the *site* has changed -- which is exactly the signal the
error-detection/alerting work needs.
"""

import json
import os

import httpx
import pytest

from web_scrapers.austender import austender
from web_scrapers.common import RECORD_NAME
from web_scrapers.qld_qtenders import qld_qtenders as qld
from web_scrapers.nt_qtol import nt_qtol as nt
from web_scrapers.vic_buyingfor import vic_buyingfor as vic
from web_scrapers.wa_tenders import wa_tenders as wa

pytestmark = pytest.mark.live

VIC_TENDER_LIST = "https://www.tenders.vic.gov.au/tenders/open"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(follow_redirects=True, timeout=30.0) as http_client:
        yield http_client


class TestAusTenderReachable:
    def test_the_listing_page_responds(self, client):
        response = client.get(austender.ATM_LIST_URL, headers=austender.HEADERS)

        assert response.status_code == 200

    def test_the_listing_page_still_contains_tender_links(self, client):
        response = client.get(austender.ATM_LIST_URL, headers=austender.HEADERS)

        tenders = austender.parse_listing(response.text)

        assert tenders, "no /Atm/Show/ links on the listing page -- markup changed?"
        assert any(t["title"] for t in tenders), (
            "no tender titles on the listing page -- the ingestion record needs one"
        )

    def test_a_real_detail_page_still_parses(self, client):
        listing = client.get(austender.ATM_LIST_URL, headers=austender.HEADERS)
        detail_url = austender.parse_listing(listing.text)[0]["url"]

        response = client.get(detail_url, headers=austender.HEADERS)
        detail = austender.parse_detail(response.text, detail_url)

        assert detail["atm_id"]
        assert detail["metadata"], "no list-desc fields found -- markup changed?"
        assert detail["documents_url"], "no ATM Documents link -- markup changed?"


class TestTendersWaReachable:
    """
    WA is plain HTTP, but it is stateful: the search will not run without the
    session cookie and CSRF nonce the home page hands out. These tests fail
    loudly if either of those stops working, which is not something the offline
    fixtures can tell us.
    """

    @pytest.fixture(scope="class")
    def nonce(self, client):
        return wa.open_session(client)

    def test_the_home_page_still_issues_a_csrf_nonce(self, nonce):
        assert nonce, "no CSRFNONCE on the WA home page -- the search will not run"

    def test_the_search_returns_advertised_tenders(self, client, nonce):
        response = client.get(wa.search_url(nonce), headers=wa.HEADERS, timeout=60.0)

        assert response.status_code == 200
        entries = wa.parse_listing(response.text)
        assert entries, "no rows in #tenderSearchResultsTable -- markup changed?"
        assert all(e["reference"] for e in entries), "a tender with no reference number"
        assert any(e["has_documents"] for e in entries)

    def test_a_real_detail_page_still_parses(self, client, nonce):
        response = client.get(wa.search_url(nonce), headers=wa.HEADERS, timeout=60.0)
        entry = next(e for e in wa.parse_listing(response.text) if e["has_documents"])

        page = client.get(entry["url"], headers=wa.HEADERS, timeout=45.0)
        parsed = wa.parse_detail(page.text, entry["url"])

        assert parsed["title"], "no title on the detail page -- ingestion requires one"
        assert parsed["reference"], "no Number: field -- markup changed?"
        assert parsed["specs_url"], "no Download Now link on a tender with documents"
        assert parsed["documents"], "documents advertised on the listing but none parsed"


class TestTendersWaDocuments:
    def test_the_bundle_needs_a_login_when_we_have_no_session(self, client, tmp_path):
        """
        The expected outcome without credentials, and the thing that must be
        reported as requires_login rather than as an empty document list --
        otherwise the tender looks like it simply has no attachments.
        """
        nonce = wa.open_session(client)
        listing = client.get(wa.search_url(nonce), headers=wa.HEADERS, timeout=60.0)
        entry = next(e for e in wa.parse_listing(listing.text) if e["has_documents"])
        page = client.get(entry["url"], headers=wa.HEADERS, timeout=45.0)
        parsed = wa.parse_detail(page.text, entry["url"])

        if os.environ.get("WA_TENDERS_COOKIE"):
            pytest.skip("a session cookie is configured; this asserts the anonymous path")

        requires_login = wa.download_specifications(
            client, parsed["specs_url"], tmp_path, parsed["documents"]
        )

        assert requires_login is True
        assert list(tmp_path.iterdir()) == []


class TestNtQtolReachable:
    """
    QTOL serves its results from the fragment endpoint the page's own
    JavaScript calls. If that endpoint moves or stops reporting its record
    count, the scraper would quietly take one page and call it a day -- which
    is the failure these tests exist to catch.
    """

    @pytest.fixture(scope="class")
    def first_page(self, client):
        return client.get(nt.search_url(page=1), headers=nt.HEADERS, timeout=60.0)

    def test_the_search_endpoint_still_answers(self, first_page):
        assert first_page.status_code == 200

    def test_it_still_reports_how_many_tenders_there_are(self, first_page):
        total = nt.total_records(first_page.text)

        assert total, "no data-total-records -- paging would stop after one page"

    def test_the_page_size_we_ask_for_is_the_page_size_we_get(self, client, first_page):
        """
        QTOL falls back to 5 for any size outside its own select, silently.
        A full page proves 20 was honoured.
        """
        total = nt.total_records(first_page.text) or 0
        entries = nt.parse_listing(first_page.text)

        if total < nt.PAGE_SIZE:
            pytest.skip(f"only {total} current tenders; cannot fill a page")
        assert len(entries) == nt.PAGE_SIZE

    def test_the_cards_still_carry_the_fields_the_record_needs(self, first_page):
        entries = nt.parse_listing(first_page.text)

        assert entries, "no .tender-card elements -- markup changed?"
        assert all(e["reference"] for e in entries), "a tender with no reference"
        assert all(e["title"] for e in entries), "ingestion requires a title"
        assert any(e["description"] for e in entries)

    def test_a_real_detail_page_still_parses(self, client, first_page):
        entry = nt.parse_listing(first_page.text)[0]

        page = client.get(entry["url"], headers=nt.HEADERS, timeout=45.0)
        parsed = nt.parse_detail(page.text, entry["url"])

        assert parsed["download_url"], "no Download tender link -- markup changed?"
        assert parsed["agency"], "no 'Listed by' block -- markup changed?"


class TestNtQtolDocuments:
    def test_the_profile_needs_an_account(self, client, tmp_path):
        if os.environ.get("NT_QTOL_COOKIE"):
            pytest.skip("a session cookie is configured; this asserts the anonymous path")

        listing = client.get(nt.search_url(page=1), headers=nt.HEADERS, timeout=60.0)
        entry = nt.parse_listing(listing.text)[0]
        page = client.get(entry["url"], headers=nt.HEADERS, timeout=45.0)
        parsed = nt.parse_detail(page.text, entry["url"])

        documents, requires_login = nt.collect_documents(
            client, parsed["download_url"], tmp_path
        )

        assert documents == []
        assert requires_login is True


class TestAusTenderDocuments:
    def test_documents_need_a_login_when_we_have_no_credentials(self, client):
        listing = client.get(austender.ATM_LIST_URL, headers=austender.HEADERS)
        detail_url = austender.parse_listing(listing.text)[0]["url"]
        detail = austender.parse_detail(
            client.get(detail_url, headers=austender.HEADERS).text, detail_url
        )

        documents, requires_login = austender.collect_documents(
            client, detail["documents_url"]
        )

        # Anonymously AusTender redirects to its login form. If this ever stops
        # being true the scraper can drop the credential handling entirely.
        assert requires_login is True
        assert documents == []

    @pytest.mark.skipif(
        not (os.environ.get("AUSTENDER_USERNAME") and os.environ.get("AUSTENDER_PASSWORD")),
        reason="set AUSTENDER_USERNAME / AUSTENDER_PASSWORD to run",
    )
    def test_a_real_tender_downloads_into_its_own_directory(self, output_dir):
        username, password = austender.credentials()

        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            assert austender.log_in(client, username, password), "login failed"

            listing = client.get(austender.ATM_LIST_URL, headers=austender.HEADERS)
            tender = austender.parse_listing(listing.text)[0]
            record = austender.scrape_tender(
                client, tender["url"], output_dir, title=tender.get("title")
            )

        reference = record["source_reference_id"]
        folder = output_dir / reference
        written = json.loads((folder / RECORD_NAME).read_text(encoding="utf-8"))
        scrape = written["raw_extra"]["scrape"]

        assert (folder / f"{reference}.txt").stat().st_size > 0
        assert scrape["documents_advertised"] > 0, "tender advertised no documents"
        assert scrape["documents_downloaded"] == scrape["documents_advertised"], (
            f"only got {scrape['documents_downloaded']} of "
            f"{scrape['documents_advertised']}: "
            f"{[d['error'] for d in scrape['documents_detail'] if d['error']]}"
        )
        # Every downloaded document is a real file sitting beside the text file.
        for document in scrape["documents_detail"]:
            assert (folder / document["local_path"]).stat().st_size > 0


def vic_driver():
    """Build the VIC scraper's UC-mode browser, headless unless VISIBLE is set."""
    return vic.build_driver(headless=os.environ.get("VISIBLE", "") == "")


class TestVictoriaReachable:
    def test_the_open_tenders_page_loads_past_cloudflare(self):
        # Plain HTTP is challenged by Cloudflare, so this needs the same UC-mode
        # browser the scraper uses. It is the slowest test in the suite.
        driver = vic_driver()
        try:
            vic.open_page(driver, VIC_TENDER_LIST)

            assert not vic.looks_blocked(driver), (
                "Cloudflare blocked us -- try VISIBLE=1, or a different network"
            )
            assert vic.determine_page_count(driver) >= 1
        finally:
            driver.quit()

    def test_a_real_tender_page_still_lists_its_documents(self):
        driver = vic_driver()
        try:
            vic.open_page(driver, VIC_TENDER_LIST)
            from selenium.webdriver.support.ui import WebDriverWait

            wait = WebDriverWait(driver, 30)
            tender = vic.links_on_current_page(driver, wait, 1)[0]
            vic.open_page(driver, tender["url"])

            html = driver.page_source
            documents = vic.parse_specification_documents(html)

            # Anonymously the names are listed but the links are not; that is
            # the state the manifest has to describe honestly.
            assert vic.specs_require_login(html) or documents
        finally:
            driver.quit()


class TestQueenslandReachable:
    def test_a_real_vendorpanel_detail_page_still_parses(self, client):
        tenders = qld.read_saved_urls()
        detail = qld.parse_detail(qld.fetch_detail(client, tenders[0]["url"]))

        assert detail["ref"] or detail["title"], "VendorPanel markup changed?"
        assert isinstance(detail["documents"], int)
