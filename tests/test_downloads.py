"""
Downloading tender documents.

These run against a real HTTP server on localhost rather than a mocked
transport, so the streaming read, the status handling and the
Content-Disposition parsing are all genuinely exercised -- that is where
download bugs actually live.
"""

import httpx
import pytest

from web_scrapers.common import Document, download_document, tender_dir

PDF_BYTES = b"%PDF-1.4\n" + b"x" * 5000 + b"\n%%EOF"
LOGIN_HTML = b"<html><body><form><input type='password'></form></body></html>"


@pytest.fixture
def client():
    with httpx.Client(follow_redirects=True) as http_client:
        yield http_client


@pytest.fixture
def portal(local_site):
    """A fake portal with one real attachment on it."""
    base_url, root = local_site
    (root / "Attachment A.pdf").write_bytes(PDF_BYTES)
    return base_url, root


class TestSuccessfulDownload:
    def test_file_lands_in_the_tender_directory(self, client, portal, output_dir):
        base_url, _ = portal
        folder = tender_dir("ABC-1", output_dir)
        document = Document(
            file_name="Attachment A.pdf", url=f"{base_url}/Attachment%20A.pdf"
        )

        download_document(client, document, folder)

        assert document.downloaded is True
        assert document.error is None
        assert (folder / "Attachment A.pdf").read_bytes() == PDF_BYTES

    def test_records_where_it_went_and_how_big_it_was(self, client, portal, output_dir):
        base_url, _ = portal
        folder = tender_dir("ABC-1", output_dir)
        document = Document(
            file_name="Attachment A.pdf", url=f"{base_url}/Attachment%20A.pdf"
        )

        download_document(client, document, folder)

        assert document.local_path == "Attachment A.pdf"
        assert document.bytes_written == len(PDF_BYTES)

    def test_large_files_stream_through_intact(self, client, local_site, output_dir):
        base_url, root = local_site
        payload = bytes(range(256)) * 40_000  # ~10 MB, many chunks
        (root / "big.bin").write_bytes(payload)
        folder = tender_dir("ABC-1", output_dir)
        document = Document(file_name="big.bin", url=f"{base_url}/big.bin")

        download_document(client, document, folder)

        assert (folder / "big.bin").read_bytes() == payload

    def test_two_documents_of_the_same_name_are_both_kept(
        self, client, local_site, output_dir
    ):
        base_url, root = local_site
        (root / "v1").mkdir()
        (root / "v2").mkdir()
        (root / "v1" / "Addendum.pdf").write_bytes(b"first version")
        (root / "v2" / "Addendum.pdf").write_bytes(b"second version")
        folder = tender_dir("ABC-1", output_dir)

        for path in ("v1", "v2"):
            download_document(
                client,
                Document(file_name="Addendum.pdf", url=f"{base_url}/{path}/Addendum.pdf"),
                folder,
            )

        assert (folder / "Addendum.pdf").read_bytes() == b"first version"
        assert (folder / "Addendum (2).pdf").read_bytes() == b"second version"


class TestFailedDownload:
    def test_missing_file_is_recorded_not_raised(self, client, portal, output_dir):
        base_url, _ = portal
        folder = tender_dir("ABC-1", output_dir)
        document = Document(file_name="gone.pdf", url=f"{base_url}/gone.pdf")

        download_document(client, document, folder)

        assert document.downloaded is False
        assert "404" in document.error
        assert list(folder.iterdir()) == []  # no truncated leftovers

    def test_unreachable_host_is_recorded_not_raised(self, client, output_dir):
        folder = tender_dir("ABC-1", output_dir)
        # Port 1 on loopback: nothing listens there, so this fails to connect.
        document = Document(file_name="x.pdf", url="http://127.0.0.1:1/x.pdf")

        download_document(client, document, folder)

        assert document.downloaded is False
        assert document.error

    def test_a_login_page_served_instead_of_the_file_is_not_saved(
        self, client, local_site, output_dir
    ):
        base_url, root = local_site
        # The portal redirects the download to its login form and answers 200
        # with HTML. Saving that as "Attachment.pdf" would quietly corrupt the
        # tender with a page of markup where a document should be.
        (root / "login.html").write_bytes(LOGIN_HTML)
        folder = tender_dir("ABC-1", output_dir)
        document = Document(file_name="Attachment.pdf", url=f"{base_url}/login.html")

        download_document(client, document, folder)

        assert document.downloaded is False
        assert "login" in document.error.lower()
        assert not (folder / "Attachment.pdf").exists()

    def test_empty_response_is_treated_as_a_failure(
        self, client, local_site, output_dir
    ):
        base_url, root = local_site
        (root / "empty.pdf").write_bytes(b"")
        folder = tender_dir("ABC-1", output_dir)
        document = Document(file_name="empty.pdf", url=f"{base_url}/empty.pdf")

        download_document(client, document, folder)

        assert document.downloaded is False
        assert "empty" in document.error
        assert not (folder / "empty.pdf").exists()

    def test_document_with_no_link_is_reported_as_such(self, client, output_dir):
        folder = tender_dir("ABC-1", output_dir)
        document = Document(file_name="Behind a login.pdf", url=None)

        download_document(client, document, folder)

        assert document.downloaded is False
        assert "no download URL" in document.error


class TestFilenameFromServer:
    def test_a_crafted_filename_cannot_escape_the_tender_directory(
        self, client, local_site, output_dir, tmp_path
    ):
        base_url, root = local_site
        (root / "doc").write_bytes(PDF_BYTES)
        folder = tender_dir("ABC-1", output_dir)
        document = Document(file_name="../../../pwned.pdf", url=f"{base_url}/doc")

        download_document(client, document, folder)

        assert document.downloaded is True
        assert (folder / "pwned.pdf").exists()
        assert not (tmp_path / "pwned.pdf").exists()

    def test_extension_is_inferred_when_the_scraped_name_has_none(
        self, client, local_site, output_dir
    ):
        base_url, root = local_site
        (root / "doc.pdf").write_bytes(PDF_BYTES)
        folder = tender_dir("ABC-1", output_dir)
        # Some portals hand back "document" with the real type only in the
        # Content-Type header.
        document = Document(file_name="document", url=f"{base_url}/doc.pdf")

        download_document(client, document, folder)

        assert document.local_path == "document.pdf"
