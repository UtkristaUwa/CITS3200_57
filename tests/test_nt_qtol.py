"""
Quotations and Tenders Online (NT): paging the search, parsing a tender, and
establishing that its documents are behind an account.

Written before the scraper. QTOL renders its results server-side and serves
them from a fragment endpoint, so everything here is plain HTTP against
trimmed captures of the real pages -- no browser, no credentials.
"""

import httpx
import pytest

from conftest import read_fixture
from web_scrapers.nt_qtol import nt_qtol as nt


@pytest.fixture
def listing():
    return read_fixture("nt_list.html")


@pytest.fixture
def detail():
    return read_fixture("nt_detail.html")


class TestSearchPaging:
    def test_the_search_url_asks_for_the_largest_page_the_portal_honours(self):
        url = nt.search_url(page=1)

        assert "/Tender/SearchResults/Current" in url
        assert "size=20" in url
        assert "page=1" in url

    def test_a_size_the_portal_rejects_is_not_sent(self):
        """
        The page-size select offers 5, 10, 15 and 20. Ask for 100 and QTOL
        silently falls back to 5, so a run that thought it had everything
        would quietly take a fifth of it.
        """
        assert "size=20" in nt.search_url(page=1, size=100)
        assert "size=10" in nt.search_url(page=1, size=10)

    def test_reads_the_record_count_off_the_results_div(self, listing):
        assert nt.total_records(listing) == 36

    def test_a_missing_count_is_none_not_zero(self):
        """Zero would end the paging loop; None means 'keep going until empty'."""
        assert nt.total_records("<div id='tender-search-results'></div>") is None

    def test_works_out_how_many_pages_to_walk(self):
        assert nt.page_count(36, size=20) == 2
        assert nt.page_count(20, size=20) == 1
        assert nt.page_count(0, size=20) == 0
        assert nt.page_count(None, size=20) is None


class TestListing:
    def test_finds_every_card(self, listing):
        assert len(nt.parse_listing(listing)) == 3

    def test_splits_the_request_type_off_the_reference(self, listing):
        """QTOL prints them as one string: 'Quote T26-1279'."""
        first = nt.parse_listing(listing)[0]

        assert first["reference"] == "T26-1279"
        assert first["type_label"] == "Quote"

    def test_reads_the_rest_of_the_card(self, listing):
        first = nt.parse_listing(listing)[0]

        assert first["agency"] == "Department of Logistics and Infrastructure"
        assert first["title"].startswith("Katherine Region")
        assert first["category"] == "Civil"
        assert first["status_label"] == "Current"
        assert first["closing_date"] == "21/09/2026 02:00 PM ACST"
        assert "excavation of the pavement" in first["description"]

    def test_carries_the_description_the_detail_page_repeats(self, listing):
        """
        The card holds the full description, so a tender is still usable if
        its detail page fails -- the ingestion record has something to say.
        """
        assert all(entry["description"] for entry in nt.parse_listing(listing))

    def test_builds_absolute_stable_detail_urls(self, listing):
        """
        The href carries ?status=Current, which is a view filter rather than
        part of the tender's identity -- keeping it would change source_url
        the day the tender closes.
        """
        for entry in nt.parse_listing(listing):
            assert entry["url"].startswith("https://tendersonline.nt.gov.au/Tender/Details/")
            assert "status=" not in entry["url"]

    def test_an_empty_result_set_is_not_an_error(self):
        assert nt.parse_listing('<div id="tender-search-results"></div>') == []


class TestDetail:
    def test_reads_the_milestone_dates(self, detail):
        parsed = nt.parse_detail(detail, "https://example.test/t/1")

        assert parsed["release_date"] == "10/09/2026"
        assert parsed["closing_date"] == "21/09/2026 2:00 PM ACST"

    def test_an_undecided_award_date_is_not_mistaken_for_one(self, detail):
        """QTOL prints 'To be determined' in the same cell shape as a date."""
        assert nt.parse_detail(detail, "https://example.test/t/1")["award_date"] is None

    def test_reads_the_labelled_fields(self, detail):
        parsed = nt.parse_detail(detail, "https://example.test/t/1")

        assert parsed["category"] == "Civil"
        assert parsed["procurement_method"] == "Public"

    def test_reads_the_region_of_works(self, detail):
        assert nt.parse_detail(detail, "https://example.test/t/1")["region"] == "Big Rivers"

    def test_reads_the_listing_agency_and_its_address(self, detail):
        parsed = nt.parse_detail(detail, "https://example.test/t/1")

        assert parsed["agency"] == "Department of Logistics and Infrastructure"
        assert "GPO Box 2520" in parsed["lodgment_address"]

    def test_reads_the_enquiries_phone(self, detail):
        parsed = nt.parse_detail(detail, "https://example.test/t/1")

        assert "8973 8691" in parsed["contact_phone"]

    def test_finds_the_document_download(self, detail):
        parsed = nt.parse_detail(detail, "https://example.test/t/1")

        assert parsed["download_url"].endswith("/Tender/DownloadProfile/26396")

    def test_a_shell_page_parses_to_nulls_rather_than_raising(self):
        parsed = nt.parse_detail("<html><body></body></html>", "https://example.test/t/1")

        assert parsed["download_url"] is None
        assert parsed["release_date"] is None


class TestDocuments:
    """
    QTOL advertises no document names at all -- one "Download tender" button,
    behind an account with a registered business. So the honest output is an
    empty document list *with* requires_login set, which is the distinction
    raw_extra.scrape exists to record.
    """

    def test_an_anonymous_download_is_reported_as_needing_a_login(
        self, local_site, output_dir
    ):
        base_url, root = local_site
        (root / "LogOn").write_text(
            "<html><body><form><input type='password'></form></body></html>"
        )

        with httpx.Client(follow_redirects=True) as client:
            documents, requires_login = nt.collect_documents(
                client, f"{base_url}/LogOn", output_dir
            )

        assert documents == []
        assert requires_login is True
        assert list(output_dir.iterdir()) == []

    def test_a_real_bundle_is_unpacked_when_the_session_allows_it(
        self, local_site, output_dir
    ):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Conditions of Tender.pdf", b"%PDF-1.4 body")
            archive.writestr("Specification.docx", b"docx body")

        base_url, root = local_site
        (root / "profile.zip").write_bytes(buffer.getvalue())

        with httpx.Client(follow_redirects=True) as client:
            documents, requires_login = nt.collect_documents(
                client, f"{base_url}/profile.zip", output_dir
            )

        assert requires_login is False
        assert {d.file_name for d in documents} == {
            "Conditions of Tender.pdf",
            "Specification.docx",
        }
        assert all(document.downloaded for document in documents)
        assert (output_dir / "Specification.docx").read_bytes() == b"docx body"

    def test_no_download_link_means_nothing_to_do(self, output_dir):
        with httpx.Client() as client:
            assert nt.collect_documents(client, None, output_dir) == ([], False)

    def test_a_broken_download_is_recorded_not_raised(self, local_site, output_dir):
        base_url, _ = local_site

        with httpx.Client(follow_redirects=True) as client:
            documents, requires_login = nt.collect_documents(
                client, f"{base_url}/gone.zip", output_dir
            )

        assert documents == []
        assert requires_login is False


class TestRecord:
    @pytest.fixture
    def record(self, listing, detail):
        entry = nt.parse_listing(listing)[0]
        parsed = nt.parse_detail(detail, entry["url"])
        return nt.build_tender_record(parsed, entry, entry["url"], [], True)

    def test_maps_the_tender_onto_ingestions_shape(self, record):
        assert record["source_id"] == "nt-qtol"
        assert record["source_reference_id"] == "T26-1279"
        assert record["title"].startswith("Katherine Region")
        assert record["issuing_agency"] == "Department of Logistics and Infrastructure"
        assert record["status"] == "open"

    def test_normalises_the_day_first_dates(self, record):
        """
        QTOL prints 21/09/2026. Read as month-first that is a valid date in a
        different month, which is the kind of error nothing downstream can see.
        """
        assert record["publish_date"] == "2026-09-10"
        assert record["closing_date"] == "2026-09-21"

    def test_a_quote_is_classified_as_an_rfq_not_a_tender(self, record):
        """
        QTOL says "Quote" where the other portals say "request for quotation",
        so the portal's own word is mapped here rather than by widening the
        shared classifier -- which would start reading any tender whose title
        happens to mention a quote as an RFQ.
        """
        assert record["category"] == "rfq"

    def test_maps_the_portals_request_type_vocabulary(self):
        assert nt.classify_request_type("Quote") == "rfq"
        assert nt.classify_request_type("Tender") == "tender"
        assert nt.classify_request_type("EOI") == "eoi"

    def test_an_unfamiliar_request_type_falls_back_to_the_shared_classifier(self):
        assert nt.classify_request_type("", "Expression of Interest for X") == "eoi"
        assert nt.classify_request_type("Something new", "Request for Tender") == "tender"

    def test_an_unclassifiable_type_is_left_null_rather_than_guessed(self):
        assert nt.classify_request_type("Something new", "Something new") is None

    def test_records_that_the_documents_need_an_account(self, record):
        scrape = record["raw_extra"]["scrape"]

        assert scrape["documents_require_login"] is True
        assert scrape["documents_advertised"] == 0

    def test_keeps_the_portals_own_fields_in_raw_extra(self, record):
        assert record["raw_extra"]["procurement_method"] == "Public"
        assert record["raw_extra"]["region_of_works"] == "Big Rivers"
