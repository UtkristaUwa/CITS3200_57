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
from web_scrapers.vic_buyingfor import vic_buyingfor as vic

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
