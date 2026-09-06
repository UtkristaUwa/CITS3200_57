"""
QTenders (Queensland) scraper.

Tender detail pages live on VendorPanel. The public preview prints a document
*count* but no filenames or links -- those need a registered supplier account.
The tests pin both halves: the count is recorded faithfully, and the download
path works the moment links do appear.
"""

import json

import pytest

from conftest import read_fixture
from web_scrapers.common import MANIFEST_NAME
from web_scrapers.qld_qtenders import qld_qtenders as qld

PREVIEW_URL = (
    "https://www.vendorpanel.com.au/PublicTenderPreviewPop.aspx"
    "?id=817f38d97d1349d3a654cc9e4736d612s522442"
)


class TestDetailPage:
    @pytest.fixture
    def detail(self):
        return qld.parse_detail(read_fixture("qld_vendorpanel_detail.html"))

    def test_extracts_the_vp_reference_used_as_the_directory_name(self, detail):
        assert detail["ref"] == "VP522442"

    def test_extracts_the_title(self, detail):
        assert detail["title"].startswith("North and Far North Tropical Low")

    def test_keeps_fields_under_the_section_they_belong_to(self, detail):
        sections = {section for section, _, _ in detail["fields"]}

        # "Business Name" appears under more than one section, so the section
        # has to travel with the field or the values get mixed up.
        assert len(sections) > 1
        assert all(label for _, label, _ in detail["fields"])

    def test_reads_the_attachment_count(self, detail):
        assert detail["documents"] == 5

    def test_no_attachments_reads_as_zero_not_as_text(self):
        html = (
            "<div class='opportunityPreviewMinHeading'>Documents</div>"
            "<div class='opportunityPreviewContent'>None attached</div>"
        )

        assert qld.parse_detail(html)["documents"] == 0


class TestDocumentLinks:
    def test_the_public_preview_offers_no_downloadable_links(self):
        detail = qld.parse_detail(read_fixture("qld_vendorpanel_detail.html"))

        assert detail["document_links"] == []

    def test_links_are_picked_up_when_a_signed_in_page_renders_them(self):
        html = (
            "<a title='Tender Documents.pdf' "
            "href='/DownloadTenderDocument.aspx?id=42'>Download</a>"
        )

        documents = qld.parse_detail(html)["document_links"]

        assert len(documents) == 1
        assert documents[0].file_name == "Tender Documents.pdf"
        assert documents[0].url == (
            "https://www.vendorpanel.com.au/DownloadTenderDocument.aspx?id=42"
        )


class TestReferenceFallback:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("https://www.vendorpanel.com.au/x.aspx?id=abcs522442", "VP522442"),
            ("https://www.vendorpanel.com.au/x.aspx?id=nodigits", ""),
        ],
    )
    def test_reference_can_be_recovered_from_the_url(self, url, expected):
        assert qld.ref_from_url(url) == expected


class TestFormattedText:
    def test_renders_the_sections_and_the_attachment_note(self):
        detail = qld.parse_detail(read_fixture("qld_vendorpanel_detail.html"))

        text = qld.format_detail(PREVIEW_URL, detail)

        assert "QTENDERS (VENDORPANEL) DETAILS - VP522442" in text
        assert PREVIEW_URL in text
        assert "5 attachment(s)" in text


class TestOutputFormat:
    @pytest.fixture
    def scraped(self, local_site, output_dir, monkeypatch):
        base_url, root = local_site
        (root / "detail.html").write_text(
            read_fixture("qld_vendorpanel_detail.html"), encoding="utf-8"
        )

        records, failed = qld.save_details(
            [{"ref": "VP522442", "url": f"{base_url}/detail.html"}], output_dir
        )
        assert not failed
        return records[0], output_dir / "VP522442"

    def test_writes_one_directory_named_after_the_reference(self, scraped):
        _, folder = scraped

        assert folder.is_dir()
        assert (folder / "VP522442.txt").exists()

    def test_manifest_records_every_attachment_as_needing_a_login(self, scraped):
        _, folder = scraped
        manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))

        assert manifest["source_id"] == "qld-qtenders"
        assert manifest["documents_require_login"] is True
        # All five are listed as placeholders, so the gap is visible rather than
        # looking like a tender with no attachments at all.
        assert manifest["documents_advertised"] == 5
        assert manifest["documents_downloaded"] == 0
        assert all(
            "VendorPanel supplier login" in document["error"]
            for document in manifest["documents"]
        )

    def test_an_unreachable_tender_is_reported_not_raised(self, local_site, output_dir):
        base_url, _ = local_site

        records, failed = qld.save_details(
            [{"ref": "VP1", "url": f"{base_url}/missing.html"}], output_dir
        )

        assert records == []
        assert len(failed) == 1


class TestUrlListIsNotLoadBearing:
    def test_a_read_only_install_directory_does_not_lose_the_scrape(
        self, tmp_path, monkeypatch
    ):
        """
        The URL list is written next to the source file, which is only writable
        in the container because the job happens to run as root. It is a
        debugging aid for SKIP_COLLECT reruns -- failing to write it must never
        cost us the tenders we just collected.
        """
        read_only = tmp_path / "read_only"
        read_only.mkdir(mode=0o500)
        monkeypatch.setattr(qld, "OUTPUT_FILE", read_only / "qld_qtenders_urls.txt")

        class FakeDriver:
            def quit(self):
                pass

        monkeypatch.setattr(qld, "build_driver", lambda headless: FakeDriver())
        monkeypatch.setattr(qld, "load_page", lambda *a, **k: (1, 1, 1))
        monkeypatch.setattr(
            qld,
            "links_on_current_page",
            lambda driver: [{"ref": "VP1", "title": "One", "url": "https://x/1"}],
        )

        tenders = qld.collect_tenders(headless=True, max_pages=1)

        assert [tender["ref"] for tender in tenders] == ["VP1"]
