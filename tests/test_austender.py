"""
AusTender scraper: parsing the real pages, and an end-to-end scrape against a
fake portal served from localhost.

The end-to-end test is the one that matters for the output format -- it drives
`scrape_tender` over real HTTP and then asserts on what landed on disk.
"""

import json

import httpx
import pytest

from conftest import read_fixture
from web_scrapers.austender import austender
from web_scrapers.common import MANIFEST_NAME

DETAIL_URL = "https://www.tenders.gov.au/Atm/Show/0cfa9d36-97d4-495f-8065-eb1a9048fb63"
PDF_BYTES = b"%PDF-1.4\n" + b"tender attachment" * 50


class TestListingPage:
    def test_finds_the_tender_detail_links(self):
        urls = austender.parse_listing(read_fixture("austender_list.html"))

        assert urls
        assert all("/Atm/Show/" in url for url in urls)
        assert all(url.startswith("https://www.tenders.gov.au/") for url in urls)

    def test_repeated_links_are_returned_once(self):
        html = '<a href="/Atm/Show/abc">One</a><a href="/Atm/Show/abc">Same</a>'

        assert austender.parse_listing(html) == [
            "https://www.tenders.gov.au/Atm/Show/abc"
        ]

    def test_a_page_with_no_tenders_yields_nothing(self):
        assert austender.parse_listing("<html><body>No results</body></html>") == []


class TestDetailPage:
    def test_extracts_the_atm_id_used_as_the_directory_name(self):
        detail = austender.parse_detail(read_fixture("austender_detail.html"), DETAIL_URL)

        assert detail["atm_id"] == "ATM_2026_3494"

    def test_extracts_the_structured_attributes(self):
        detail = austender.parse_detail(read_fixture("austender_detail.html"), DETAIL_URL)

        assert detail["metadata"]["Agency"] == (
            "Department of Industry, Science and Resources"
        )
        assert "Close Date & Time" in detail["metadata"]
        assert len(detail["metadata"]) > 10

    def test_finds_the_link_to_the_documents_page(self):
        detail = austender.parse_detail(read_fixture("austender_detail.html"), DETAIL_URL)

        assert detail["documents_url"] == (
            "https://www.tenders.gov.au/Atm/ViewDocuments/"
            "0cfa9d36-97d4-495f-8065-eb1a9048fb63"
        )

    def test_a_tender_with_no_readable_id_falls_back_to_the_url(self):
        detail = austender.parse_detail("<html><body></body></html>", DETAIL_URL)

        # Still unique per tender, so two such tenders cannot share a folder.
        assert detail["atm_id"] == "0cfa9d36-97d4-495f-8065-eb1a9048fb63"


class TestDocumentsPage:
    def test_lists_every_attachment_and_addendum(self):
        documents = austender.parse_documents(read_fixture("austender_documents.html"))

        assert len(documents) == 4
        assert all(document.url for document in documents)

    def test_takes_the_filename_from_the_title_attribute(self):
        documents = austender.parse_documents(read_fixture("austender_documents.html"))

        assert documents[0].file_name == "Approach to Market - Example Tender.docx"

    def test_falls_back_to_the_filename_query_parameter(self):
        documents = austender.parse_documents(read_fixture("austender_documents.html"))

        # Third link has no title attribute, only ?fileName=Addendum%201.pdf
        assert documents[2].file_name == "Addendum 1.pdf"

    def test_falls_back_to_a_placeholder_when_the_page_names_nothing(self):
        documents = austender.parse_documents(read_fixture("austender_documents.html"))

        assert documents[3].file_name == "document.bin"

    def test_ignores_links_that_are_not_downloads(self):
        documents = austender.parse_documents(read_fixture("austender_documents.html"))

        assert not any("/Atm/Show/" in document.url for document in documents)

    def test_recognises_the_login_form_served_to_anonymous_visitors(self):
        assert austender.looks_like_login_page(read_fixture("austender_login.html"))
        assert not austender.looks_like_login_page(
            read_fixture("austender_documents.html")
        )


@pytest.fixture
def fake_austender(local_site, monkeypatch):
    """
    A stand-in AusTender on localhost, laid out on the real routes.

    Pointing the module's BASE_URL at it means the scraper's own navigation --
    listing -> detail -> documents -> file -- is what gets exercised, over real
    HTTP, without touching the live portal.
    """
    base_url, root = local_site

    for path in ("atm", "Atm/Show", "Atm/ViewDocuments", "Atm/DownloadSoftCopy"):
        (root / path).mkdir(parents=True, exist_ok=True)

    (root / "atm" / "index.html").write_text(
        '<a href="/Atm/Show/tender-one">Tender one</a>', encoding="utf-8"
    )
    (root / "Atm" / "Show" / "tender-one").write_text(
        """<html><body>
        <p class="lead">Current ATM View - LOCAL_1</p>
        <div class="list-desc"><span><label>ATM ID</label></span>
          <div class="list-desc-inner">LOCAL_1</div></div>
        <div class="list-desc"><span><label>Agency</label></span>
          <div class="list-desc-inner">Test Agency</div></div>
        <a class="rBtn" href="/Atm/ViewDocuments/tender-one">ATM Documents</a>
        </body></html>""",
        encoding="utf-8",
    )
    (root / "Atm" / "ViewDocuments" / "tender-one").write_text(
        '<a title="Statement of Requirements.pdf" '
        'href="/Atm/DownloadSoftCopy/sor.pdf">Download</a>',
        encoding="utf-8",
    )
    (root / "Atm" / "DownloadSoftCopy" / "sor.pdf").write_bytes(PDF_BYTES)

    monkeypatch.setattr(austender, "BASE_URL", base_url)
    monkeypatch.setattr(austender, "ATM_LIST_URL", f"{base_url}/atm")
    return base_url


class TestScrapeTenderEndToEnd:
    @pytest.fixture
    def scraped(self, fake_austender, output_dir):
        with httpx.Client(follow_redirects=True) as client:
            record = austender.scrape_tender(
                client, f"{fake_austender}/Atm/Show/tender-one", output_dir, pause=0
            )
        return record, output_dir / "LOCAL_1"

    def test_writes_one_directory_named_after_the_tender(self, scraped):
        _, folder = scraped

        assert folder.is_dir()

    def test_writes_the_scraped_text_into_that_directory(self, scraped):
        _, folder = scraped
        text = (folder / "LOCAL_1.txt").read_text(encoding="utf-8")

        assert "AUSTENDER DETAILS" in text
        assert "ATM ID: LOCAL_1" in text
        assert "Agency: Test Agency" in text

    def test_downloads_the_attachment_into_the_same_directory(self, scraped):
        _, folder = scraped

        assert (folder / "Statement of Requirements.pdf").read_bytes() == PDF_BYTES

    def test_writes_a_manifest_describing_the_attachment(self, scraped):
        _, folder = scraped
        manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))

        assert manifest["reference"] == "LOCAL_1"
        assert manifest["source_id"] == "austender"
        assert manifest["documents_advertised"] == 1
        assert manifest["documents_downloaded"] == 1
        assert manifest["documents"][0]["local_path"] == "Statement of Requirements.pdf"

    def test_the_directory_holds_exactly_the_expected_files(self, scraped):
        _, folder = scraped

        assert sorted(p.name for p in folder.iterdir()) == [
            "LOCAL_1.txt",
            "Statement of Requirements.pdf",
            MANIFEST_NAME,
        ]


class TestScrapeRunEndToEnd:
    def test_run_scraper_writes_one_directory_per_tender(
        self, fake_austender, output_dir, monkeypatch
    ):
        monkeypatch.delenv("AUSTENDER_USERNAME", raising=False)
        monkeypatch.delenv("AUSTENDER_PASSWORD", raising=False)

        records = austender.run_scraper(limit=1, output_dir=output_dir, pause=0)

        assert len(records) == 1
        assert [p.name for p in output_dir.iterdir()] == ["LOCAL_1"]

    def test_a_broken_tender_does_not_abort_the_run(
        self, fake_austender, output_dir, monkeypatch
    ):
        # One good link and one that 404s: the run must still produce the good one.
        monkeypatch.setattr(
            austender,
            "parse_listing",
            lambda html: [
                f"{fake_austender}/Atm/Show/does-not-exist",
                f"{fake_austender}/Atm/Show/tender-one",
            ],
        )

        records = austender.run_scraper(limit=2, output_dir=output_dir, pause=0)

        assert len(records) == 1
        assert (output_dir / "LOCAL_1" / "LOCAL_1.txt").exists()


class TestCredentials:
    def test_absent_credentials_are_reported_as_none(self, monkeypatch):
        monkeypatch.delenv("AUSTENDER_USERNAME", raising=False)
        monkeypatch.delenv("AUSTENDER_PASSWORD", raising=False)

        assert austender.credentials() == (None, None)

    def test_credentials_come_from_the_environment_not_the_source(self, monkeypatch):
        monkeypatch.setenv("AUSTENDER_USERNAME", "someone@example.test")
        monkeypatch.setenv("AUSTENDER_PASSWORD", "secret")

        assert austender.credentials() == ("someone@example.test", "secret")

    def test_login_replays_the_forms_hidden_anti_forgery_token(
        self, local_site, monkeypatch
    ):
        base_url, root = local_site
        (root / "login.html").write_text(
            read_fixture("austender_login.html"), encoding="utf-8"
        )
        monkeypatch.setattr(austender, "LOGIN_URL", f"{base_url}/login.html")
        monkeypatch.setattr(austender, "BASE_URL", base_url)

        posted = {}

        class RecordingClient:
            def get(self, url, **kwargs):
                return httpx.Client().get(url, **kwargs)

            def post(self, url, data=None, **kwargs):
                posted.update(data or {})
                return httpx.Response(
                    200, text="<html>Log off</html>", request=httpx.Request("POST", url)
                )

        assert austender.log_in(RecordingClient(), "user@example.test", "pw") is True
        assert posted["__RequestVerificationToken"] == "TESTTOKEN123"
        assert posted["Email"] == "user@example.test"
        assert posted["Password"] == "pw"
