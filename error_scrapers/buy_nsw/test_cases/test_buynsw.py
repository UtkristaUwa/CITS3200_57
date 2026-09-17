"""
Tests for the buy.nsw scraper, written test-first -- every function here
exists to satisfy a specific test, same discipline as
error_scrapers/grant_connect/test_cases/test_grantconnect.py.

buy.nsw needs no login (unlike GrantConnect), but adds a step
GrantConnect never needed: each opportunity's "Download opportunity
package" link is a single zip containing BOTH the real attachments AND
an auto-generated summary PDF of the whole detail page. The summary PDF
must be extracted into the tender's own page-text file and then
discarded -- it is not a real attachment, the same distinction
GrantConnect draws between save_page_text() and save_extracted_text().
"""

import io
import os
import zipfile

import pytest

from error_scrapers import common
from error_scrapers.buy_nsw import scraper

FIXTURES = os.path.dirname(__file__)


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_finds_opportunity_links():
    html = _read_fixture("buynsw_public_list.html")
    links = scraper.parse_listing(html)
    assert len(links) >= 1
    # every link found must actually be an opportunity detail page,
    # not a nav/footer/unrelated link the selector accidentally matched
    assert all("/opportunity/" in link or "/prcOpportunity/" in link for link in links)


def test_listing_links_are_absolute_and_deduplicated():
    html = _read_fixture("buynsw_public_list.html")
    links = scraper.parse_listing(html)
    assert all(link.startswith("http") for link in links)
    assert len(links) == len(set(links))


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def test_success_parses_full_details_page():
    html = _read_fixture("buynsw_full_details.html")
    fields, code = scraper.parse_detail(html)
    assert code == common.SITE_SUCCESS
    assert fields["title"] == "Provision of Mobile Devices and Accessories - GSHS9477"
    assert fields["opportunity_id"] == "GSHS9477"
    assert fields["agency"] == "NSW Department of Customer Service"
    assert fields["close_date"] == "15-Oct-2026 3:00 PM"
    assert fields["publish_date"] == "17-Sep-2026"


def test_site_structure_change_when_fields_container_missing():
    # buynsw_structure_changed.html is buynsw_full_details.html with
    # every "nsw-table-row" renamed to "nsw-table-row-CHANGED" -- the exact
    # container parse_detail() selects fields from.
    html = _read_fixture("buynsw_structure_changed.html")
    fields, code = scraper.parse_detail(html)
    assert code == common.SITE_STRUCTURE_CHANGE


def test_finds_the_opportunity_package_download_link():
    html = _read_fixture("buynsw_full_details.html")
    url = scraper.find_package_url(html)
    assert url is not None
    assert "opportunity-package-" in url
    assert url.endswith(".zip") or ".zip?" in url


def test_missing_package_link_does_not_crash():
    html = _read_fixture("buynsw_full_details.html").replace(
        "Download opportunity package", "Nothing to see here"
    )
    url = scraper.find_package_url(html)
    assert url is None


# ---------------------------------------------------------------------------
# Package download + unzip: separating the summary PDF from real attachments
# ---------------------------------------------------------------------------

def _build_fake_package_zip(summary_name="opportunity-RFT-12634672652.pdf"):
    """
    A minimal in-memory zip standing in for a real opportunity package:
    one auto-generated summary PDF (by naming convention) plus two real
    attachments. Real PDF/DOCX bytes aren't needed here -- these tests
    only check which files get treated as the summary vs. real
    attachments, not text extraction itself (that's common.py's job,
    already tested in test_grantconnect.py).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(summary_name, b"%PDF-1.4 fake summary content")
        zf.writestr("Part A - Conditions of Tendering.pdf", b"%PDF-1.4 fake part a")
        zf.writestr("Part B - Statement of Requirements.pdf", b"%PDF-1.4 fake part b")
    buf.seek(0)
    return buf.read()


def test_unzip_separates_summary_pdf_from_real_attachments(tmp_path):
    zip_bytes = _build_fake_package_zip()
    result = scraper.extract_package(
        zip_bytes,
        output_dir=str(tmp_path),
        opportunity_id="RFT-12634672652",
    )
    # the summary pdf must not appear in the real-attachments list...
    attachment_names = [a["file_name"] for a in result["attachments"]]
    assert "opportunity-RFT-12634672652.pdf" not in attachment_names
    assert "Part A - Conditions of Tendering.pdf" in attachment_names
    assert "Part B - Statement of Requirements.pdf" in attachment_names
    # ...and must not be left sitting in the output folder as a file either
    assert not os.path.exists(
        os.path.join(str(tmp_path), "opportunity-RFT-12634672652.pdf")
    )


def test_summary_pdf_becomes_the_tenders_own_page_text(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper.common, "extract_pdf", lambda path: "fake summary text")
    zip_bytes = _build_fake_package_zip()
    result = scraper.extract_package(
        zip_bytes,
        output_dir=str(tmp_path),
        opportunity_id="RFT-12634672652",
    )
    assert result["page_text"].strip() != ""
    # the page text file should be saved under the tender's own id, not
    # under the summary pdf's original filename
    assert os.path.exists(
        os.path.join(str(tmp_path), "RFT-12634672652.txt")
    )


def test_real_attachments_get_their_own_extracted_text_files(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper.common, "extract_pdf", lambda path: "fake text")
    zip_bytes = _build_fake_package_zip()
    scraper.extract_package(
        zip_bytes,
        output_dir=str(tmp_path),
        opportunity_id="RFT-12634672652",
    )
    saved = os.listdir(str(tmp_path))
    assert "Part A - Conditions of Tendering.pdf" in saved
    assert "Part A - Conditions of Tendering.pdf.txt" in saved
    assert "Part B - Statement of Requirements.pdf" in saved
    assert "Part B - Statement of Requirements.pdf.txt" in saved


def test_a_zip_with_no_recognisable_summary_pdf_does_not_crash(tmp_path):
    # if naming ever changes and nothing matches the "summary pdf" pattern,
    # every file should just be treated as a real attachment rather than
    # raising -- a missed summary costs page-text detail, not a failure.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Part A.pdf", b"%PDF-1.4 fake")
    result = scraper.extract_package(
        buf.getvalue(), output_dir=str(tmp_path), opportunity_id="RFT-999"
    )
    assert result["page_text"] == ""
    assert [a["file_name"] for a in result["attachments"]] == ["Part A.pdf"]


# ---------------------------------------------------------------------------
# Full opportunity scrape (mocked network)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, text="", content=b"", status_code=200):
        self.text = text
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("error", request=None, response=self)


class _FakeClient:
    """Stands in for httpx.Client -- routes by URL substring."""

    def __init__(self, detail_html, package_zip_bytes):
        self.detail_html = detail_html
        self.package_zip_bytes = package_zip_bytes

    def get(self, url, **kwargs):
        if url.endswith(".zip") or ".zip?" in url:
            return _FakeResponse(content=self.package_zip_bytes)
        return _FakeResponse(text=self.detail_html)


def test_scrape_opportunity_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper.common, "extract_pdf", lambda path: "fake text")
    detail_html = _read_fixture("buynsw_full_details.html")
    client = _FakeClient(detail_html, _build_fake_package_zip())
    code, tender = scraper.scrape_opportunity(
        client, "https://buy.nsw.gov.au/prcOpportunity/fake-id", str(tmp_path)
    )
    assert code == common.SITE_SUCCESS
    assert tender["title"] == "Provision of Mobile Devices and Accessories - GSHS9477"
    assert len(tender["attachments"]) == 2


def test_scrape_opportunity_still_succeeds_with_no_package_link(tmp_path):
    # "No files attached" is a real, valid state on buy.nsw (seen on the
    # live GSHS9477 fixture's Related files section) -- a tender with
    # nothing to download is not a failure.
    detail_html = _read_fixture("buynsw_full_details.html").replace(
        "Download opportunity package", "Nothing to see here"
    )
    client = _FakeClient(detail_html, b"")
    code, tender = scraper.scrape_opportunity(
        client, "https://buy.nsw.gov.au/prcOpportunity/fake-id", str(tmp_path)
    )
    assert code == common.SITE_SUCCESS
    assert tender["attachments"] == []


def test_a_corrupt_package_download_gives_tender_partial_not_a_crash(tmp_path):
    detail_html = _read_fixture("buynsw_full_details.html")
    client = _FakeClient(detail_html, b"this is not a valid zip file")
    code, tender = scraper.scrape_opportunity(
        client, "https://buy.nsw.gov.au/prcOpportunity/fake-id", str(tmp_path)
    )
    assert code == common.TENDER_PARTIAL


# ---------------------------------------------------------------------------
# Site-level failures
# ---------------------------------------------------------------------------

def test_site_total_failure_on_unreachable_url(monkeypatch):
    import httpx

    def exploding_get(*args, **kwargs):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx.Client, "get", exploding_get)
    code, tenders = scraper.run_scraper()
    assert code == common.SITE_TOTAL_FAILURE
    assert tenders == []

def test_nested_zip_gets_recursed_into(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper.common, "extract_pdf", lambda path: "fake text")
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as inner_zf:
        inner_zf.writestr("Nested Document.pdf", b"%PDF-1.4 fake nested")

    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, "w") as outer_zf:
        outer_zf.writestr("opportunity-RFT-999.pdf", b"%PDF-1.4 fake summary")
        outer_zf.writestr("Response Templates.zip", inner_buf.getvalue())

    result = scraper.extract_package(
        outer_buf.getvalue(), output_dir=str(tmp_path), opportunity_id="RFT-999"
    )
    attachment_names = [a["file_name"] for a in result["attachments"]]
    assert "Nested Document.pdf" in attachment_names
    assert "Response Templates.zip" not in attachment_names
    assert os.path.exists(os.path.join(str(tmp_path), "Nested Document.pdf"))
    assert os.path.exists(os.path.join(str(tmp_path), "Nested Document.pdf.txt"))