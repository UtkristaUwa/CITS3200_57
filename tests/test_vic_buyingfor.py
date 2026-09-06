"""
Buying for Victoria scraper.

The browser-driven parts (Cloudflare, pagination) are covered by the live
tests; what is asserted here is the parsing and the output format, which is
where the format change lands.
"""

import json

import httpx
import pytest

from conftest import read_fixture
from web_scrapers.common import MANIFEST_NAME, TenderRecord, download_document, \
    tender_dir, write_manifest, write_tender_text
from web_scrapers.vic_buyingfor import vic_buyingfor as vic


class TestPagination:
    def test_reads_the_page_count_from_the_records_summary(self):
        # "1 - 25 of 63" over a 25-row page is 3 pages, even though the pager
        # only ever lists a handful of page links.
        class FakePager:
            text = "Records: 1 - 25 of 63   1 2 3"

        class FakeDriver:
            def find_elements(self, *_):
                return [FakePager()]

        assert vic.determine_page_count(FakeDriver()) == 3

    def test_a_single_page_of_results_has_no_pager(self):
        class FakeDriver:
            def find_elements(self, *_):
                return []

        assert vic.determine_page_count(FakeDriver()) == 1


class TestSpecificationDocuments:
    def test_lists_every_document_shown_to_an_anonymous_visitor(self):
        documents = vic.parse_specification_documents(read_fixture("vic_specs.html"))

        assert [d.file_name for d in documents] == [
            "0. TAC Safe Driving Policy for Employers.PDF",
            "3. MSA Road Safety Services (FINAL) 20230303.pdf",
            "2. Prequalifiation Terms (v2.0).docx",
            "1. Invitation to Register - Road Safety Services (FINAL) 20260330.DOCX",
        ]

    def test_captures_the_version_and_size_printed_on_the_page(self):
        documents = vic.parse_specification_documents(read_fixture("vic_specs.html"))

        assert documents[0].version == "Version 1 (10 Mar 2023)"
        assert documents[0].size_label == "9 MB"
        assert documents[1].size_label == "722 KB"

    def test_anonymous_documents_have_no_link_and_say_why(self):
        documents = vic.parse_specification_documents(read_fixture("vic_specs.html"))

        assert all(document.url is None for document in documents)
        assert all("login required" in document.error for document in documents)

    def test_a_signed_in_page_yields_download_links(self):
        documents = vic.parse_specification_documents(
            read_fixture("vic_specs_authenticated.html")
        )

        assert len(documents) == 2
        assert documents[0].url == (
            "https://www.tenders.vic.gov.au/tender/download?id=249237&specId=11001"
        )
        assert all(document.error is None for document in documents)

    def test_links_are_resolved_against_the_given_base_url(self):
        documents = vic.parse_specification_documents(
            read_fixture("vic_specs_authenticated.html"), base_url="http://localhost:9"
        )

        assert documents[0].url.startswith("http://localhost:9/tender/download")

    def test_a_tender_with_no_documents_yields_none(self):
        assert vic.parse_specification_documents("<html><body></body></html>") == []


class TestLoginNotice:
    def test_detects_the_must_be_logged_in_banner(self):
        assert vic.specs_require_login(read_fixture("vic_specs.html")) is True

    def test_absent_for_a_signed_in_page(self):
        assert vic.specs_require_login(read_fixture("vic_specs_authenticated.html")) is False


class TestOutputFormat:
    """
    The write side of save_tender, without a browser: parse a real specs block,
    download whatever links it has, and check what lands on disk.
    """

    @pytest.fixture
    def scraped(self, local_site, output_dir):
        base_url, root = local_site
        (root / "tender").mkdir(parents=True, exist_ok=True)
        (root / "tender" / "download").write_bytes(b"%PDF-1.4 vic attachment")

        html = read_fixture("vic_specs_authenticated.html")
        documents = vic.parse_specification_documents(html, base_url=base_url)
        # Both fixture links point at the same route on the fake portal.
        for document in documents:
            document.url = f"{base_url}/tender/download"

        folder = tender_dir("PROCF22-000236", output_dir)
        write_tender_text(folder, "VICTORIAN GOVERNMENT TENDER - PROCF22-000236")
        with httpx.Client(follow_redirects=True) as client:
            for document in documents:
                download_document(client, document, folder)

        write_manifest(
            folder,
            TenderRecord(
                reference=folder.name,
                source_id=vic.SOURCE_ID,
                source_url="https://www.tenders.vic.gov.au/tender/view?id=249237",
                documents=documents,
                documents_require_login=vic.specs_require_login(html),
            ),
        )
        return folder

    def test_one_directory_named_after_the_rfx_number(self, scraped, output_dir):
        assert scraped == output_dir / "PROCF22-000236"

    def test_holds_the_text_file_the_manifest_and_both_attachments(self, scraped):
        assert sorted(p.name for p in scraped.iterdir()) == [
            "0. TAC Safe Driving Policy for Employers.PDF",
            "2. Prequalifiation Terms (v2.0).docx",
            "PROCF22-000236.txt",
            MANIFEST_NAME,
        ]

    def test_manifest_reports_the_source_and_the_downloads(self, scraped):
        manifest = json.loads((scraped / MANIFEST_NAME).read_text(encoding="utf-8"))

        assert manifest["source_id"] == "vic-buyingfor"
        assert manifest["documents_advertised"] == 2
        assert manifest["documents_downloaded"] == 2


class TestBlockDetection:
    @pytest.mark.parametrize(
        "title, body, blocked",
        [
            ("Attention Required! | Cloudflare", "", True),
            ("Just a moment...", "", True),
            ("Display Tender PROCF22-000236", "Road Safety Services", False),
        ],
    )
    def test_recognises_a_cloudflare_challenge(self, title, body, blocked):
        class FakeElement:
            text = body

        class FakeDriver:
            def __init__(self):
                self.title = title

            def find_element(self, *_):
                return FakeElement()

        assert vic.looks_blocked(FakeDriver()) is blocked
