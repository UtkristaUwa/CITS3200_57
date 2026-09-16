#This tests each status code per the GrantConnect scraper. We have also created fixtures for as many error codes as we can, such as failed login.

from pathlib import Path
import pytest
from error_scrapers.grant_connect import scraper
from error_scrapers import common

FIXTURES = Path(__file__).parent / "fixtures"

def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
 
#-----
#This is our login test case
#-----
 
def test_login_success_detected():
    html = load_fixture("grantconnect_login_success.html")
    assert scraper.login_succeeded(html) is True
 
def test_login_failure_detected():
    html = load_fixture("grantconnect_login_failure.html")
    assert scraper.login_succeeded(html) is False

#If a login fails, we must stop the run immediately as we dont want to scrape without the attachements which do require login
def test_site_login_failed_stops_the_run(monkeypatch):
    def fake_submit_login(client, url, payload):
        class FakeResponse:
            text = load_fixture("grantconnect_login_failure.html")
        return FakeResponse()

    monkeypatch.setattr(common, "submit_login", fake_submit_login)
    code = scraper.run_scraper()
    assert code == common.SITE_LOGIN_FAILED


#-----
#This is our full details page test case
#-----
 
def test_success_parses_full_details_page():
    html = load_fixture("grantconnect_full_details.html")
    fields, code = scraper.parse_detail(html)

    assert code == common.SUCCESS
    assert fields["go_id"] == "GO8232"
    assert fields["agency"] == "National Health and Medical Research Council (NHMRC)"
    assert fields["title"]
    assert fields["description"]

def test_site_structure_change_when_fields_container_missing():
    html = load_fixture("grantconnect_structure_changed.html")
    fields, code = scraper.parse_detail(html)
    assert code == common.SITE_STRUCTURE_CHANGE

#-----
#This is our test for the list of all the tenders
#-----
 
def test_listing_finds_opportunity_links():
    html = load_fixture("grantconnect_public_list.html")
    links = scraper.parse_listing(html)
    assert len(links) > 0
 
 
#-----
#This is our test for the attachements for a tender
#-----
 
def test_documents_page_lists_all_attachments():
    html = load_fixture("grantconnect_attachements.html")
    documents = scraper.parse_documents(html)
    # the MRFF grant captured for this fixture has 5 documents
    assert len(documents) == 5
    assert all(doc["url"] for doc in documents)
 
 
#-----
#This is our test for when a tender has a partial success on being scraped
#-----
 
def test_tender_partial_when_attachment_extraction_fails(monkeypatch):
    def broken_extract(path):
        raise common.ExtractionError("simulated failure")
 
    monkeypatch.setattr(common, "extract_pdf", broken_extract)
 
    html = load_fixture("grantconnect_attachements.html")
    documents = scraper.parse_documents(html)
    code = scraper.process_documents(documents, output_dir="/tmp/does_not_matter")
    assert code == common.TENDER_PARTIAL
 
 
#-----
#This is our total site failure test case, it has to be simulated as we ant get a fixture for this at the moment
#-----
 
def test_site_total_failure_on_unreachable_url(monkeypatch):
    def broken_get(*args, **kwargs):
        raise ConnectionError("simulated unreachable host")
 
    monkeypatch.setattr("httpx.Client.get", broken_get)
    monkeypatch.setattr("httpx.Client.post", broken_get)
 
    code = scraper.run_scraper()
    assert code == common.SITE_TOTAL_FAILURE