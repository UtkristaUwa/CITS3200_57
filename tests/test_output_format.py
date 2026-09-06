"""
The output-format contract every scraper must honour:

    tenders_data/<REF>/<REF>.txt   +   documents.json   +   the attachments

One directory per tender -- not one file per website.
"""

import json

import pytest

from web_scrapers.common import (
    Document,
    TenderRecord,
    MANIFEST_NAME,
    sanitise_filename,
    sanitise_reference,
    tender_dir,
    unique_path,
    write_manifest,
    write_tender_text,
)


class TestTenderDirectory:
    def test_creates_one_directory_named_after_the_tender(self, output_dir):
        folder = tender_dir("PROCF22-000236", output_dir)

        assert folder == output_dir / "PROCF22-000236"
        assert folder.is_dir()

    def test_text_file_is_named_after_the_directory(self, output_dir):
        folder = tender_dir("ATM_2026_3494", output_dir)

        written = write_tender_text(folder, "tender body")

        assert written == folder / "ATM_2026_3494.txt"
        assert written.read_text(encoding="utf-8") == "tender body"

    def test_two_tenders_get_two_directories(self, output_dir):
        write_tender_text(tender_dir("A26/6", output_dir), "first")
        write_tender_text(tender_dir("VP517456", output_dir), "second")

        assert sorted(p.name for p in output_dir.iterdir()) == ["A26_6", "VP517456"]
        # Nothing is written at the top level: no one-txt-per-website leftovers.
        assert all(p.is_dir() for p in output_dir.iterdir())

    def test_rescraping_a_tender_reuses_its_directory(self, output_dir):
        write_tender_text(tender_dir("ABC-1", output_dir), "first pass")
        write_tender_text(tender_dir("ABC-1", output_dir), "second pass")

        assert len(list(output_dir.iterdir())) == 1
        assert (output_dir / "ABC-1" / "ABC-1.txt").read_text() == "second pass"

    def test_attachments_sit_beside_the_text_file(self, output_dir):
        folder = tender_dir("ABC-1", output_dir)
        write_tender_text(folder, "body")
        (folder / "Attachment A.pdf").write_bytes(b"%PDF-1.4 ...")

        assert sorted(p.name for p in folder.iterdir()) == [
            "ABC-1.txt",
            "Attachment A.pdf",
        ]


class TestReferenceSanitising:
    @pytest.mark.parametrize(
        "reference, expected",
        [
            ("A26/6", "A26_6"),                      # slash would make a subdirectory
            ("PROCF22-000236", "PROCF22-000236"),    # already safe, left alone
            ("VP 517 456", "VP_517_456"),
            ("  ATM_2026_3494  ", "ATM_2026_3494"),
            ("../../escape", "escape"),
        ],
    )
    def test_produces_a_safe_single_segment_name(self, reference, expected):
        assert sanitise_reference(reference) == expected

    def test_empty_reference_falls_back(self):
        assert sanitise_reference("", fallback="tender_7") == "tender_7"
        assert sanitise_reference("///", fallback="tender_7") == "tender_7"


class TestFilenameSanitising:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("Attachment A.pdf", "Attachment A.pdf"),
            ("Bad:name?.pdf", "Bad_name_.pdf"),
            ("Attachment%20A.pdf", "Attachment A.pdf"),   # percent-decoded
            ("../../../etc/passwd", "passwd"),            # cannot escape the folder
            ("C:\\Windows\\evil.exe", "evil.exe"),
            ("", "document"),
        ],
    )
    def test_scraped_names_are_made_safe(self, name, expected):
        assert sanitise_filename(name) == expected

    def test_over_long_names_are_trimmed_but_keep_their_extension(self):
        result = sanitise_filename("x" * 400 + ".pdf")

        assert len(result) <= 180
        assert result.endswith(".pdf")


class TestDuplicateFilenames:
    def test_second_file_of_the_same_name_does_not_overwrite_the_first(self, output_dir):
        folder = tender_dir("ABC-1", output_dir)
        (folder / "Addendum.pdf").write_bytes(b"first")

        second = unique_path(folder, "Addendum.pdf")

        assert second.name == "Addendum (2).pdf"
        assert (folder / "Addendum.pdf").read_bytes() == b"first"

    def test_extensionless_duplicates_are_still_distinguished(self, output_dir):
        folder = tender_dir("ABC-1", output_dir)
        (folder / "README").write_bytes(b"first")

        assert unique_path(folder, "README").name == "README (2)"


class TestManifest:
    def test_records_what_was_downloaded_and_what_was_not(self, output_dir):
        folder = tender_dir("ABC-1", output_dir)
        record = TenderRecord(
            reference="ABC-1",
            source_id="austender",
            source_url="https://www.tenders.gov.au/Atm/Show/abc",
            documents=[
                Document(
                    file_name="Got it.pdf",
                    url="https://example.test/a.pdf",
                    downloaded=True,
                    local_path="Got it.pdf",
                    bytes_written=1234,
                ),
                Document(file_name="Missed it.pdf", error="login required"),
            ],
        )

        write_manifest(folder, record)
        payload = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))

        assert payload["reference"] == "ABC-1"
        assert payload["source_id"] == "austender"
        assert payload["documents_advertised"] == 2
        assert payload["documents_downloaded"] == 1
        assert payload["documents"][0]["local_path"] == "Got it.pdf"
        assert payload["documents"][1]["error"] == "login required"

    def test_is_written_even_when_the_tender_has_no_attachments(self, output_dir):
        folder = tender_dir("ABC-1", output_dir)

        write_manifest(
            folder,
            TenderRecord(reference="ABC-1", source_id="vic-buyingfor", source_url="u"),
        )
        payload = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))

        assert payload["documents"] == []
        assert payload["documents_advertised"] == 0
        # An empty list plus this flag is how a consumer tells "no attachments"
        # from "attachments we could not reach".
        assert payload["documents_require_login"] is False
