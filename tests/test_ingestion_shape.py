"""
The scrapers' output must line up with ingestion's tender shape.

Every tender.json a scraper writes is validated here against the real
ingestion/tender.schema.json -- not a copy -- so if that schema changes, these
tests fail rather than the scrape silently producing records that
validate_and_submit.py will reject.
"""

import json
from pathlib import Path

import jsonschema
import pytest

from conftest import REPO_ROOT, read_fixture
from web_scrapers.austender import austender
from web_scrapers.common import Document, RECORD_NAME
from web_scrapers.qld_qtenders import qld_qtenders as qld
from web_scrapers.tender_record import build_record

SCHEMA_PATH = REPO_ROOT / "ingestion" / "tender.schema.json"
SAMPLE_PATH = REPO_ROOT / "ingestion" / "sample_tender.json"


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validator(schema):
    return jsonschema.Draft7Validator(schema)


def assert_valid(validator, record):
    problems = sorted(validator.iter_errors(record), key=lambda e: list(e.path))
    assert not problems, "; ".join(
        f"{list(p.path) or '(root)'}: {p.message}" for p in problems
    )


class TestAgainstTheIngestionSample:
    def test_the_sample_itself_validates(self, validator):
        """Guards the test: if this fails, the fixtures moved, not our output."""
        for record in json.loads(SAMPLE_PATH.read_text(encoding="utf-8")):
            assert_valid(validator, record)

    def test_we_emit_the_same_field_set(self):
        sample = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))[0]

        ours = build_record(
            source_id="vic-buyingfor", source_url="https://x.test/1", title="A tender"
        )

        assert set(ours) == set(sample), (
            f"missing {set(sample) - set(ours)}, extra {set(ours) - set(sample)}"
        )

    def test_that_field_set_is_the_twenty_the_schema_allows(self, schema):
        ours = build_record(
            source_id="vic-buyingfor", source_url="https://x.test/1", title="A tender"
        )

        assert len(ours) == 20
        assert set(ours) <= set(schema["properties"])


class TestAusTenderRecord:
    @pytest.fixture
    def record(self):
        detail = austender.parse_detail(
            read_fixture("austender_detail.html"),
            "https://www.tenders.gov.au/Atm/Show/abc",
        )
        documents = austender.parse_documents(read_fixture("austender_documents.html"))
        documents[0].downloaded = True
        documents[0].local_path = documents[0].file_name
        documents[0].bytes_written = 4096
        return austender.build_tender_record(
            detail,
            "https://www.tenders.gov.au/Atm/Show/abc",
            documents,
            requires_login=False,
            title="Geological Disposal Programme Options",
        )

    def test_validates(self, validator, record):
        assert_valid(validator, record)

    def test_carries_the_real_title_not_the_page_heading(self, record):
        # The detail page only ever says "Current ATM View - <ID>".
        assert record["title"] == "Geological Disposal Programme Options"

    def test_maps_the_portals_wording_onto_the_enums(self, record):
        assert record["category"] == "tender"       # "Request for Tender"
        assert record["status"] == "open"

    def test_normalises_dates_out_of_noisy_cells(self, record):
        # "10-Sep-2026 2:00 pm (ACT Local Time) Show close time for other..."
        assert record["closing_date"] == "2026-09-10"
        assert record["publish_date"] == "2026-08-13"

    def test_documents_carry_only_schema_permitted_keys(self, record):
        for document in record["documents"]:
            assert set(document) == {
                "document_id", "file_name", "file_type", "extracted_text", "parsed_at"
            }
            assert document["file_type"] in ("pdf", "docx", "rtf", "other")

    def test_extracted_text_is_left_for_the_extraction_stage(self, record):
        assert all(d["extracted_text"] is None for d in record["documents"])

    def test_download_provenance_survives_in_raw_extra(self, record):
        scrape = record["raw_extra"]["scrape"]

        assert scrape["documents_advertised"] == 4
        assert scrape["documents_downloaded"] == 1
        assert scrape["documents_detail"][0]["local_path"]
        assert scrape["documents_detail"][0]["bytes_written"] == 4096


class TestQldRecord:
    @pytest.fixture
    def record(self):
        detail = qld.parse_detail(read_fixture("qld_vendorpanel_detail.html"))
        documents = [
            Document(file_name="(document 1 of 5)", error="requires a VendorPanel supplier login")
        ]
        return qld.build_qld_record(
            "https://www.vendorpanel.com.au/x", detail, documents, requires_login=True
        )

    def test_validates(self, validator, record):
        assert_valid(validator, record)

    def test_takes_the_agency_from_the_buyer_section(self, record):
        # "Business Name" appears under two sections; the flat lookup would
        # return whichever happened to come last.
        assert record["issuing_agency"] == "Dept of the Environ, Tourism, Science and Innov"

    def test_normalises_the_long_form_dates(self, record):
        # "Wednesday 23 September 2026 02:00 PM (E. Australia Standard Time)"
        assert record["closing_date"] == "2026-09-23"
        assert record["publish_date"] == "2026-08-25"

    def test_records_that_the_documents_need_a_login(self, record):
        assert record["raw_extra"]["scrape"]["documents_require_login"] is True
        assert record["raw_extra"]["scrape"]["documents_downloaded"] == 0


class TestWrittenToDisk:
    def test_scrape_writes_a_valid_tender_json_beside_the_text(
        self, validator, output_dir, monkeypatch, local_site
    ):
        import httpx

        base_url, root = local_site
        for path in ("Atm/Show", "Atm/ViewDocuments", "Atm/DownloadSoftCopy"):
            (root / path).mkdir(parents=True, exist_ok=True)
        (root / "Atm" / "Show" / "t1").write_text(
            """<html><body>
            <p class="lead">Current ATM View - LOCAL_1</p>
            <div class="list-desc"><span><label>ATM ID</label></span>
              <div class="list-desc-inner">LOCAL_1</div></div>
            <div class="list-desc"><span><label>Agency</label></span>
              <div class="list-desc-inner">Test Agency</div></div>
            <div class="list-desc"><span><label>ATM Type</label></span>
              <div class="list-desc-inner">Request for Tender</div></div>
            <div class="list-desc"><span><label>Close Date &amp; Time</label></span>
              <div class="list-desc-inner">10-Sep-2026 2:00 pm (ACT Local Time)</div></div>
            <a href="/Atm/ViewDocuments/t1">ATM Documents</a>
            </body></html>""",
            encoding="utf-8",
        )
        (root / "Atm" / "ViewDocuments" / "t1").write_text(
            '<a title="SOR.pdf" href="/Atm/DownloadSoftCopy/sor.pdf">Download</a>',
            encoding="utf-8",
        )
        (root / "Atm" / "DownloadSoftCopy" / "sor.pdf").write_bytes(b"%PDF-1.4 body")
        monkeypatch.setattr(austender, "BASE_URL", base_url)

        with httpx.Client(follow_redirects=True) as client:
            austender.scrape_tender(
                client, f"{base_url}/Atm/Show/t1", output_dir, pause=0, title="A Real Title"
            )

        folder = output_dir / "LOCAL_1"
        assert sorted(p.name for p in folder.iterdir()) == [
            "LOCAL_1.txt", "SOR.pdf", RECORD_NAME,
        ]

        record = json.loads((folder / RECORD_NAME).read_text(encoding="utf-8"))
        assert_valid(validator, record)
        assert record["title"] == "A Real Title"
        assert record["closing_date"] == "2026-09-10"
        assert record["raw_extra"]["scrape"]["documents_downloaded"] == 1

    def test_the_file_feeds_validate_and_submit_unchanged(
        self, validator, output_dir
    ):
        """
        validate_and_submit.py accepts a single object or a list of them, so a
        tender.json can be handed to it directly with no reshaping.
        """
        record = build_record(
            source_id="austender", source_url="https://x.test/1", title="A tender"
        )
        path = output_dir / RECORD_NAME
        path.write_text(json.dumps(record), encoding="utf-8")

        loaded = json.loads(path.read_text(encoding="utf-8"))
        records = loaded if isinstance(loaded, list) else [loaded]

        for entry in records:
            assert_valid(validator, entry)
