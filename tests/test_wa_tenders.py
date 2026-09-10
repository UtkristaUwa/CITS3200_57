"""
Tenders WA: parsing the advertised-requests list and a tender's details, and
getting the specification bundle out of the portal.

Written before the scraper, and offline: the list and detail fixtures are
trimmed captures of the real pages, and the bundle download runs against a
real HTTP server on localhost rather than a mocked transport, because the
streaming-download path is the part most likely to break.

The portal shape that drives all of this: WA publishes no URL per document.
The page lists names, versions and types, and a single "Download Now" link
hands over every file at once as a zip, behind a registered-user session.
"""

import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import httpx
import pytest

from conftest import read_fixture
from web_scrapers import common
from web_scrapers.wa_tenders import wa_tenders as wa


@pytest.fixture
def listing():
    return read_fixture("wa_list.html")


@pytest.fixture
def detail():
    return read_fixture("wa_detail.html")


def make_zip(files):
    """An in-memory zip of {name: content}."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class TestListing:
    def test_finds_every_advertised_tender(self, listing):
        assert len(wa.parse_listing(listing)) == 3

    def test_reads_the_fields_the_detail_page_does_not_carry(self, listing):
        """
        The listing is the only place the issuing agency and the UNSPSC
        category appear as their own cells; the detail page runs them together
        with the title.
        """
        first = wa.parse_listing(listing)[0]

        assert first["reference"] == "DEMOTENDER"
        assert first["title"].startswith("DEMONSTRATION TENDER")
        assert first["agency"] == "Demonstration Agency"
        assert first["category"] == "Education and Training Services"
        assert first["closing_date"] == "31 Dec, 2027 2:30 PM"
        assert first["type_label"] == "Tender"
        assert first["status_label"] == "Current"

    def test_builds_absolute_detail_urls(self, listing):
        for entry in wa.parse_listing(listing):
            assert entry["url"].startswith("https://www.tenders.wa.gov.au/watenders/")
            assert "tender-details.action" in entry["url"]

    def test_lists_each_tender_once_despite_the_duplicate_links(self, listing):
        """The title and the attachment icon are two links to the same page."""
        references = [entry["reference"] for entry in wa.parse_listing(listing)]

        assert len(references) == len(set(references))

    def test_notes_which_tenders_advertise_documents(self, listing):
        by_reference = {e["reference"]: e for e in wa.parse_listing(listing)}

        assert by_reference["RFP112025FSa"]["has_documents"] is True
        assert by_reference["WC7000015480"]["has_documents"] is False

    def test_an_empty_page_is_not_an_error(self):
        assert wa.parse_listing("<html><body>nothing here</body></html>") == []


class TestDetail:
    def test_reads_the_title_without_the_agency_line_glued_to_it(self, detail):
        """
        Both live in one <div>, separated by a <br>. Taking the div's text
        would title the tender "Sale of Industrial Wood and Forest Residue
        Issued by Forest Products Commission".
        """
        parsed = wa.parse_detail(detail, "https://example.test/t/1")

        assert parsed["title"] == "Sale of Industrial Wood and Forest Residue"
        assert parsed["agency"] == "Forest Products Commission"

    def test_reads_the_labelled_fields(self, detail):
        parsed = wa.parse_detail(detail, "https://example.test/t/1")

        assert parsed["reference"] == "RFP112025FSa"
        assert parsed["status_label"] == "Current"
        assert parsed["unspsc"] == "Forestry - (100%)"
        assert "Goldfields/Esperance" in parsed["regions"]
        assert "Wheatbelt" in parsed["regions"]

    def test_unescapes_the_description_out_of_its_textarea(self, detail):
        """
        WA puts the description in a <textarea> as escaped HTML, so the raw
        text is a wall of &lt;p&gt;. Left as-is it would reach the AI stage
        as markup.
        """
        description = wa.parse_detail(detail, "https://example.test/t/1")["description"]

        assert "&lt;" not in description
        assert "<p>" not in description
        assert "Forest Products Commission (FPC) invites applications" in description

    def test_reads_the_closing_date(self, detail):
        parsed = wa.parse_detail(detail, "https://example.test/t/1")

        assert parsed["closing_date"] == "Thu, 31 Dec 2026 at 3:00PM"

    def test_reads_the_contact(self, detail):
        parsed = wa.parse_detail(detail, "https://example.test/t/1")

        assert parsed["contact_name"] == "Erika Steel"
        assert parsed["contact_email"] == "business.enquiries@fpc.wa.gov.au"
        assert "93634646" in parsed["contact_phone"]

    def test_finds_the_bundle_link(self, detail):
        parsed = wa.parse_detail(detail, "https://example.test/t/1")

        assert parsed["specs_url"].endswith(
            "/watenders/tender/downloadspecs.action?id=67874&method=downloadViaHTML"
        )

    def test_a_page_missing_everything_still_parses(self):
        """A portal outage serves a shell page; it must not raise."""
        parsed = wa.parse_detail("<html><body></body></html>", "https://example.test/t/1")

        assert parsed["title"] is None
        assert parsed["documents"] == []
        assert parsed["specs_url"] is None


class TestAdvertisedDocuments:
    def test_lists_every_document_with_its_version(self, detail):
        documents = wa.parse_detail(detail, "https://example.test/t/1")["documents"]

        assert len(documents) >= 6
        first = documents[0]
        assert first.file_name == (
            "RFP112025FSa - Part A and B - Plantation and FMP24-33 Panel.pdf"
        )
        assert first.version == "Ver 1 - 19 Feb, 2026"

    def test_does_not_mistake_the_status_table_for_documents(self, detail):
        """
        'Status:', 'Number:' and 'UNSPSC:' are marked up with the same
        span.LIST_TITLE class as a document name, one table higher up the page.
        """
        names = [
            d.file_name
            for d in wa.parse_detail(detail, "https://example.test/t/1")["documents"]
        ]

        assert not {"Status:", "Number:", "UNSPSC:", "Region/s:"} & set(names)

    def test_carries_no_per_document_url(self, detail):
        """
        There isn't one. Inventing a URL here would make download_document
        write the same bundle under every advertised name.
        """
        documents = wa.parse_detail(detail, "https://example.test/t/1")["documents"]

        assert all(document.url is None for document in documents)


class TestSpecificationBundle:
    """
    Site access and document downloading -- the two things worth a test on any
    portal, and on this one they are the same request.
    """

    @pytest.fixture
    def documents(self):
        return [
            common.Document(file_name="Part A.pdf"),
            common.Document(file_name="Appendix B.docx"),
        ]

    def test_an_anonymous_request_is_reported_as_needing_a_login(
        self, local_site, output_dir, documents
    ):
        base_url, root = local_site
        (root / "login.action").write_text(
            "<html><body><form><input type='password'></form></body></html>"
        )

        with httpx.Client(follow_redirects=True) as client:
            requires_login = wa.download_specifications(
                client, f"{base_url}/login.action", output_dir, documents
            )

        assert requires_login is True
        assert not any(document.downloaded for document in documents)
        assert list(output_dir.iterdir()) == []

    def test_unpacks_the_bundle_next_to_the_tender_text(
        self, local_site, output_dir, documents
    ):
        base_url, root = local_site
        (root / "specs.zip").write_bytes(
            make_zip({"Part A.pdf": b"%PDF-1.4 body", "Appendix B.docx": b"docx body"})
        )

        with httpx.Client(follow_redirects=True) as client:
            requires_login = wa.download_specifications(
                client, f"{base_url}/specs.zip", output_dir, documents
            )

        assert requires_login is False
        assert (output_dir / "Part A.pdf").read_bytes() == b"%PDF-1.4 body"
        assert (output_dir / "Appendix B.docx").read_bytes() == b"docx body"

    def test_matches_extracted_files_back_to_what_was_advertised(
        self, local_site, output_dir, documents
    ):
        base_url, root = local_site
        (root / "specs.zip").write_bytes(
            make_zip({"Part A.pdf": b"12345", "Appendix B.docx": b"678"})
        )

        with httpx.Client(follow_redirects=True) as client:
            wa.download_specifications(
                client, f"{base_url}/specs.zip", output_dir, documents
            )

        assert [d.downloaded for d in documents] == [True, True]
        assert documents[0].local_path == "Part A.pdf"
        assert documents[0].bytes_written == 5

    def test_matches_on_the_name_even_when_the_zip_nests_it(
        self, local_site, output_dir, documents
    ):
        """Some bundles carry a folder per tender inside the archive."""
        base_url, root = local_site
        (root / "specs.zip").write_bytes(
            make_zip({"RFP112025FSa/Part A.pdf": b"12345"})
        )

        with httpx.Client(follow_redirects=True) as client:
            wa.download_specifications(
                client, f"{base_url}/specs.zip", output_dir, documents
            )

        assert documents[0].downloaded is True
        assert (output_dir / "Part A.pdf").exists()

    def test_a_file_in_the_bundle_that_was_not_advertised_is_still_kept(
        self, local_site, output_dir, documents
    ):
        base_url, root = local_site
        (root / "specs.zip").write_bytes(
            make_zip({"Part A.pdf": b"a", "Late Addendum 4.pdf": b"b"})
        )

        with httpx.Client(follow_redirects=True) as client:
            wa.download_specifications(
                client, f"{base_url}/specs.zip", output_dir, documents
            )

        assert (output_dir / "Late Addendum 4.pdf").exists()

    def test_a_member_cannot_escape_the_tender_folder(
        self, local_site, output_dir, documents
    ):
        """
        The archive comes from outside our control, so a member named
        ../../evil.txt must land inside the folder or not at all.
        """
        base_url, root = local_site
        (root / "specs.zip").write_bytes(
            make_zip({"../../evil.txt": b"pwned", "Part A.pdf": b"fine"})
        )

        with httpx.Client(follow_redirects=True) as client:
            wa.download_specifications(
                client, f"{base_url}/specs.zip", output_dir, documents
            )

        escaped = output_dir.parent.parent / "evil.txt"
        assert not escaped.exists()
        assert (output_dir / "evil.txt").exists()
        assert (output_dir / "Part A.pdf").exists()

    def test_something_that_is_not_an_archive_is_recorded_not_raised(
        self, local_site, output_dir, documents
    ):
        base_url, root = local_site
        (root / "specs.zip").write_bytes(b"not a zip at all")

        with httpx.Client(follow_redirects=True) as client:
            requires_login = wa.download_specifications(
                client, f"{base_url}/specs.zip", output_dir, documents
            )

        assert requires_login is False
        assert all(document.error for document in documents)

    def test_a_single_unzipped_document_is_saved_under_its_own_name(
        self, local_site, output_dir
    ):
        """A tender with one specification is served as that file, not a zip."""
        base_url, root = local_site
        (root / "Part A.pdf").write_bytes(b"%PDF-1.4 single")
        documents = [common.Document(file_name="Part A.pdf")]

        with httpx.Client(follow_redirects=True) as client:
            wa.download_specifications(
                client, f"{base_url}/Part A.pdf", output_dir, documents
            )

        assert documents[0].downloaded is True
        assert (output_dir / "Part A.pdf").read_bytes() == b"%PDF-1.4 single"

    def test_a_missing_bundle_is_an_error_on_every_document(
        self, local_site, output_dir, documents
    ):
        base_url, _ = local_site

        with httpx.Client(follow_redirects=True) as client:
            requires_login = wa.download_specifications(
                client, f"{base_url}/gone.zip", output_dir, documents
            )

        assert requires_login is False
        assert all("404" in (document.error or "") for document in documents)

    def test_no_bundle_link_means_nothing_to_do(self, output_dir, documents):
        with httpx.Client() as client:
            assert wa.download_specifications(client, None, output_dir, documents) is False


LOGIN_FORM = """<html><body>
<form method="post" id="loginform" action="/login.action?action=login&CSRFNONCE=NONCE1">
  <input type="hidden" name="struts.token" value="TOKEN">
  <input type="text" name="userName">
  <input type="password" name="password">
</form></body></html>"""


@pytest.fixture
def login_site():
    """
    A stand-in portal that serves the login form and records what was posted.

    The shared local_site fixture serves files and answers 501 to a POST, which
    is the one method this needs to exercise.
    """
    posted = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._reply(LOGIN_FORM.encode())

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            posted.update(parse_qs(self.rfile.read(length).decode()))
            posted["path"] = self.path
            self._reply(b"signed in")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", posted
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class TestSession:
    def test_reads_the_csrf_nonce_the_search_url_needs(self):
        html = '<a href="/watenders/x.action?action=y&CSRFNONCE=ABC123DEF">x</a>'

        assert wa.csrf_nonce(html) == "ABC123DEF"

    def test_a_page_without_one_is_not_an_error(self):
        assert wa.csrf_nonce("<html></html>") is None

    def test_the_search_url_carries_the_nonce(self):
        url = wa.search_url("ABC123")

        assert "advanced-tender-search-open-tender" in url
        assert url.endswith("CSRFNONCE=ABC123")

    def test_the_search_url_works_without_one(self):
        """A session that never got a nonce should still try, not crash."""
        assert "CSRFNONCE" not in wa.search_url(None)

    def test_credentials_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("WA_TENDERS_USERNAME", "user")
        monkeypatch.setenv("WA_TENDERS_PASSWORD", "secret")

        assert wa.credentials() == ("user", "secret")

    def test_missing_credentials_are_none_not_empty_strings(self, monkeypatch):
        monkeypatch.delenv("WA_TENDERS_USERNAME", raising=False)
        monkeypatch.delenv("WA_TENDERS_PASSWORD", raising=False)

        assert wa.credentials() == (None, None)

    def test_a_session_cookie_can_stand_in_for_the_login(self, monkeypatch):
        """
        The portal emails a token at login, so a username and password alone
        may not get a session. A cookie lifted from a signed-in browser is the
        route that always works, and it is what the credentials arrangement
        with Ramon can realistically supply.
        """
        monkeypatch.setenv("WA_TENDERS_COOKIE", "JSESSIONID=abc123")

        client = httpx.Client()
        wa.apply_session_cookie(client)

        assert client.cookies.get("JSESSIONID") == "abc123"

    def test_no_cookie_leaves_the_client_alone(self, monkeypatch):
        monkeypatch.delenv("WA_TENDERS_COOKIE", raising=False)

        client = httpx.Client()
        wa.apply_session_cookie(client)

        assert not dict(client.cookies)

    def test_a_malformed_cookie_is_ignored_rather_than_crashing_the_run(
        self, monkeypatch
    ):
        monkeypatch.setenv("WA_TENDERS_COOKIE", "not a cookie")

        client = httpx.Client()
        wa.apply_session_cookie(client)

        assert not dict(client.cookies)

    def test_logging_in_replays_the_forms_hidden_fields(self, login_site):
        """
        The form carries a CSRF nonce as a hidden input *and* another in its
        own action URL. Posting a fixed payload would drop both, and the portal
        would reject the login for a reason nothing here could see.
        """
        base_url, posted = login_site

        with httpx.Client(follow_redirects=True) as client:
            assert wa.log_in(client, "user", "secret", f"{base_url}/index.do") is True

        assert posted["struts.token"] == ["TOKEN"]
        assert posted["userName"] == ["user"]
        assert posted["password"] == ["secret"]
        assert "CSRFNONCE=NONCE1" in posted["path"]

    def test_a_login_page_with_no_form_is_reported_not_raised(self, local_site):
        base_url, root = local_site
        (root / "index.do").write_text("<html><body>maintenance</body></html>")

        with httpx.Client(follow_redirects=True) as client:
            assert wa.log_in(client, "user", "secret", f"{base_url}/index.do") is False


class TestRecord:
    """The scraped tender has to validate against ingestion's schema."""

    def test_maps_the_page_onto_ingestions_shape(self, detail, listing):
        entry = [e for e in wa.parse_listing(listing) if e["reference"] == "RFP112025FSa"][0]
        parsed = wa.parse_detail(detail, entry["url"])

        record = wa.build_tender_record(parsed, entry, entry["url"], parsed["documents"], True)

        assert record["source_id"] == "wa-tenders"
        assert record["source_reference_id"] == "RFP112025FSa"
        assert record["title"] == "Sale of Industrial Wood and Forest Residue"
        assert record["issuing_agency"] == "Forest Products Commission"
        assert record["status"] == "open"
        assert record["closing_date"] == "2026-12-31"
        assert record["contact_email"] == "business.enquiries@fpc.wa.gov.au"
        assert record["raw_extra"]["scrape"]["documents_require_login"] is True

    def test_the_listing_supplies_what_the_detail_page_lacks(self, detail, listing):
        entry = [e for e in wa.parse_listing(listing) if e["reference"] == "RFP112025FSa"][0]
        parsed = wa.parse_detail(detail, entry["url"])

        record = wa.build_tender_record(parsed, entry, entry["url"], [], False)

        assert record["raw_extra"]["unspsc_category"]

    def test_wa_publishes_no_advertised_date(self, detail, listing):
        """
        Recorded as null rather than guessed from the closing date -- a wrong
        publish_date silently misfiles the tender in any date-ordered view.
        """
        entry = wa.parse_listing(listing)[0]
        record = wa.build_tender_record(
            wa.parse_detail(detail, entry["url"]), entry, entry["url"], [], False
        )

        assert record["publish_date"] is None
