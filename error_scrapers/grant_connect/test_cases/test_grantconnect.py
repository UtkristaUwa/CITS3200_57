#This tests each status code per the GrantConnect scraper. We have also created fixtures for as many error codes as we can, such as failed login.

from pathlib import Path
import pytest
from error_scrapers.grant_connect import scraper
from error_scrapers import common
import httpx

FIXTURES = Path(__file__).parent

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
    code, _ = scraper.run_scraper()
    assert code == common.SITE_LOGIN_FAILED


#-----
#This is our full details page test case
#-----
 
def test_success_parses_full_details_page():
    html = load_fixture("grantconnect_full_details.html")
    fields, code = scraper.parse_detail(html)

    assert code == common.SITE_SUCCESS
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
    with httpx.Client() as client:
        code, _ = scraper.process_documents(client, documents, output_dir="/tmp/does_not_matter")
    assert code == common.TENDER_PARTIAL
 
 
#-----
#This is our total site failure test case, it has to be simulated as we ant get a fixture for this at the moment
#-----
 
def test_site_total_failure_on_unreachable_url(monkeypatch):
    def broken_get(*args, **kwargs):
        raise ConnectionError("simulated unreachable host")
 
    monkeypatch.setattr("httpx.Client.get", broken_get)
    monkeypatch.setattr("httpx.Client.post", broken_get)
 
    code, _ = scraper.run_scraper()
    assert code == common.SITE_TOTAL_FAILURE


#-----
#These cover what process_documents returns, which the storage step depends on:
#the list of files actually written to disk, and the fact that a file we have no
#extractor for is not a failure.
#-----

class _FakeStream:
    """Stands in for one httpx streaming response."""

    def __init__(self, data: bytes, content_type: str):
        self.headers = {"content-type": content_type}
        self._data = data

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        yield self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeClient:
    """A client whose downloads always succeed, so no network is touched."""

    def __init__(self, data: bytes = b"not really a file",
                 content_type: str = "application/octet-stream"):
        self._data = data
        self._content_type = content_type

    def stream(self, method, url, **kwargs):
        return _FakeStream(self._data, self._content_type)


def test_unsupported_file_type_is_saved_but_not_a_failure(tmp_path):
    documents = [{"file_name": "budget.csv", "url": "https://example.test/budget.csv"}]

    code, attachments = scraper.process_documents(
        _FakeClient(), documents, output_dir=str(tmp_path)
    )

    assert code == common.SITE_SUCCESS
    assert [a["file_name"] for a in attachments] == ["budget.csv"]
    assert (tmp_path / "budget.csv").exists()
    # nothing to extract from a csv, so no text file should appear
    assert not (tmp_path / "budget.csv.txt").exists()


def test_duplicate_attachment_names_do_not_overwrite(tmp_path):
    documents = [
        {"file_name": "Attachment 1.csv", "url": "https://example.test/a"},
        {"file_name": "Attachment 1.csv", "url": "https://example.test/b"},
    ]

    code, attachments = scraper.process_documents(
        _FakeClient(), documents, output_dir=str(tmp_path)
    )

    assert code == common.SITE_SUCCESS
    assert [a["file_name"] for a in attachments] == [
        "Attachment 1.csv",
        "Attachment 1 (2).csv",
    ]


def test_attachments_report_size_and_content_type(tmp_path):
    documents = [{"file_name": "notes.csv", "url": "https://example.test/notes.csv"}]

    _, attachments = scraper.process_documents(
        _FakeClient(data=b"abcdef", content_type="text/csv"),
        documents,
        output_dir=str(tmp_path),
    )

    assert attachments[0]["size_bytes"] == 6
    assert attachments[0]["content_type"] == "text/csv"
