"""
Web scraper for Tenders WA (https://www.tenders.wa.gov.au) -- WA state agencies.

Output: one directory per tender under tenders_data/, containing the scraped
detail text as <REFERENCE>.txt, a tender.json ingestion record, and every
specification document that could be downloaded.

Plain HTTP throughout -- no browser. The portal is a server-rendered Struts
application: it hands out a session cookie and a CSRF nonce on the home page,
and the advertised-requests search returns every current tender on a single
page, so there is no pagination to walk.

Documents:
    WA publishes no URL per document. The tender page lists names, versions and
    types, and one "Download Now" link hands over the whole set at once, behind
    a registered-user session. So the download is all-or-nothing: either the
    bundle arrives and is unpacked into the tender's folder, or the manifest
    records documents_require_login and the names stay visible.

Login:
    WA_TENDERS_COOKIE  a session cookie from a signed-in browser, e.g.
                       "JSESSIONID=..." -- the reliable route, because the
                       portal emails a verification token at login.
    WA_TENDERS_USERNAME / WA_TENDERS_PASSWORD
                       submitted to the login form when no cookie is set. This
                       works only for accounts the portal does not challenge
                       for a token.

    Without either the scraper still runs and still writes one folder per
    tender.

Usage:
    python -m web_scrapers.wa_tenders.wa_tenders --limit 10
"""

import argparse
import io
import logging
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from web_scrapers.common import (
    Document,
    filename_from_response,
    sanitise_filename,
    tender_dir,
    unique_path,
    write_tender_record,
    write_tender_text,
)
from web_scrapers.tender_record import (
    build_record,
    classify_category,
    classify_status,
    first_email,
)

log = logging.getLogger(__name__)

SOURCE_ID = "wa-tenders"
BASE_URL = "https://www.tenders.wa.gov.au"
INDEX_URL = f"{BASE_URL}/watenders/index.do"
SEARCH_PATH = "/watenders/tender/search/tender-search.action"
DETAIL_PATH = "/watenders/tender/display/tender-details.action"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

CSRF_PATTERN = re.compile(r"CSRFNONCE=([0-9A-Za-z]+)")
TENDER_ID_PATTERN = re.compile(r"[?&]id=(\d+)")
VERSION_PATTERN = re.compile(r"^\((.*)\)$", re.S)
CLOSES_PATTERN = re.compile(r"^\s*Closes\s+", re.I)
ISSUED_BY_PATTERN = re.compile(r"^\s*Issued by\s+(.*)$", re.I | re.S)

# The portal prints the closing time with its timezone spelled out
# ("... at 3:00PM Perth, Western Australia"). The date parser copes, but the
# raw field reads better without it.
TIMEZONE_SUFFIX = re.compile(r"\s+Perth,\s.*$", re.I)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def credentials():
    """Return (username, password) from the environment, or (None, None)."""
    return (
        os.environ.get("WA_TENDERS_USERNAME") or None,
        os.environ.get("WA_TENDERS_PASSWORD") or None,
    )


def apply_session_cookie(client):
    """
    Put WA_TENDERS_COOKIE on the client, if one is configured.

    Preferred over a username and password because the portal emails a
    verification token at login, which nothing here can answer. A cookie
    captured from a signed-in browser sidesteps that; it expires, which is
    why its absence is a warning and not an error.

    Returns True when a cookie was applied.
    """
    raw = os.environ.get("WA_TENDERS_COOKIE", "").strip()
    if not raw:
        return False

    applied = False
    for part in raw.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            client.cookies.set(name.strip(), value.strip(), domain="www.tenders.wa.gov.au")
            applied = True

    if not applied:
        log.warning("WA_TENDERS_COOKIE is set but is not a name=value pair -- ignoring")
    return applied


def csrf_nonce(html):
    """The CSRF nonce the portal stamps into every one of its own links."""
    match = CSRF_PATTERN.search(html or "")
    return match.group(1) if match else None


def search_url(nonce):
    """The advertised-requests search, which lists every current tender."""
    url = f"{BASE_URL}{SEARCH_PATH}?action=advanced-tender-search-open-tender"
    return f"{url}&CSRFNONCE={nonce}" if nonce else url


def detail_url(tender_id):
    """
    A stable URL for one tender.

    The links on the listing carry a returnUrl with the session's CSRF nonce
    baked into it, which changes on every run. Rebuilding the URL from the id
    keeps `source_url` the same tender from one day to the next, which is what
    anything downstream will key on.
    """
    return f"{BASE_URL}{DETAIL_PATH}?id={tender_id}&action=display-tender-details"


def open_session(client):
    """Fetch the home page for a session cookie and a nonce. Returns the nonce."""
    response = client.get(INDEX_URL, headers=HEADERS, timeout=25.0)
    response.raise_for_status()
    return csrf_nonce(response.text)


def log_in(client, username, password, login_page_url=INDEX_URL):
    """
    Submit the portal's login form on `client`.

    Every hidden input is read back off the form and replayed: the form carries
    a CSRF nonce as a hidden field *and* another in its own action URL, and
    posting a fixed payload would drop both.

    Returns True if the form was found and submitted without a transport error.
    Whether the session is actually authenticated is answered later, by whether
    the specification bundle resolves -- the portal may still want an emailed
    token, and it says so on a page that looks like a successful response.
    """
    try:
        response = client.get(login_page_url, headers=HEADERS, timeout=25.0)
        response.raise_for_status()
    except Exception as exc:
        log.error("could not load the login page: %s", exc)
        return False

    form = BeautifulSoup(response.text, "html.parser").select_one(
        "form#loginform, form[action*='login.action'], form"
    )
    if form is None:
        log.error("no login form at %s -- has the page changed?", login_page_url)
        return False

    payload = {
        field.get("name"): field.get("value", "")
        for field in form.select("input[type='hidden']")
        if field.get("name")
    }
    user_input = form.select_one(
        "input[type='text'], input[name*='ser'], input[name*='mail']"
    )
    password_input = form.select_one("input[type='password'], input[name*='ass']")
    payload[user_input.get("name") if user_input else "userName"] = username
    payload[password_input.get("name") if password_input else "password"] = password

    post_url = urljoin(str(response.url), form.get("action") or login_page_url)
    try:
        result = client.post(
            post_url,
            data=payload,
            headers=dict(HEADERS, Referer=str(response.url)),
            follow_redirects=True,
            timeout=30.0,
        )
    except Exception as exc:
        log.error("login POST failed: %s", exc)
        return False

    if result.status_code >= 400:
        log.error("login POST returned HTTP %s", result.status_code)
        return False

    log.info("submitted Tenders WA credentials; session cookies set")
    return True


def looks_like_login_page(html):
    """True if the portal served its login form instead of what was asked for."""
    return (
        BeautifulSoup(html or "", "html.parser").select_one("input[type='password']")
        is not None
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_listing(html, base_url=BASE_URL):
    """
    Return one entry per tender on the advertised-requests page.

    The listing is the only place the issuing agency and the UNSPSC category
    appear as fields of their own -- on the detail page the agency is run
    together with the title in a single div. Both cells are hidden in the
    browser (they exist for the CSV export), which is why they are read
    positionally rather than by class.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    table = soup.select_one("#tenderSearchResultsTable")
    if table is None:
        return []

    entries = []
    for row in table.select("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            continue
        link = row.select_one(f"a[href*='{DETAIL_PATH.rsplit('/', 1)[-1]}']")
        if link is None:
            continue

        identifier = TENDER_ID_PATTERN.search(link.get("href") or "")
        if identifier is None:
            continue

        # The reference cell is <b>REF</b><br><span class=tenderState>..</span>
        # <br>Type. Pull the first two out, and whatever is left is the type.
        reference_cell = cells[2]
        reference_tag = reference_cell.find("b")
        reference = reference_tag.get_text(strip=True) if reference_tag else None
        state_tag = reference_cell.select_one("span.tenderState")
        status_label = state_tag.get_text(strip=True) if state_tag else None
        if state_tag is not None:
            state_tag.extract()
        if reference_tag is not None:
            reference_tag.extract()
        type_label = reference_cell.get_text(" ", strip=True) or None

        # The title is the first of the two links to the tender; the second
        # wraps the attachment icon and has no text.
        title = None
        for anchor in row.select(f"a[href*='{DETAIL_PATH.rsplit('/', 1)[-1]}']"):
            text = anchor.get_text(" ", strip=True)
            if text:
                title = text
                break

        closing = row.select_one("span.SUMMARY_CLOSINGDATE")

        entries.append(
            {
                "tender_id": identifier.group(1),
                "url": detail_url(identifier.group(1)),
                "reference": reference,
                "title": title,
                "agency": cells[0].get_text(" ", strip=True) or None,
                "category": cells[1].get_text(" ", strip=True) or None,
                "closing_date": closing.get_text(" ", strip=True) if closing else None,
                "status_label": status_label,
                "type_label": type_label,
                "has_documents": row.select_one("img[alt='specification documents']")
                is not None,
            }
        )
    return entries


def parse_labelled_fields(soup):
    """The Status / Number / UNSPSC / Region block, as a dict."""
    fields = {}
    for row in soup.select("table.nospace2 tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 2:
            continue
        label = cells[0].select_one("span.LIST_TITLE")
        if label is None:
            continue
        key = label.get_text(strip=True).rstrip(":")
        lines = [line for line in cells[1].get_text("\n", strip=True).split("\n") if line]
        fields[key] = ", ".join(lines)
    return fields


def parse_description(soup):
    """
    The description, out of its textarea and out of its markup.

    WA stores it as escaped HTML inside a <textarea>, so the raw value is a
    wall of &lt;p&gt;. Unescaped once by the parser, then stripped of tags --
    otherwise the AI stage is handed markup to summarise.
    """
    textarea = soup.select_one("#description textarea, textarea#desc")
    if textarea is None:
        return None
    inner = BeautifulSoup(textarea.get_text(), "html.parser").get_text("\n", strip=True)
    return inner or None


def parse_contact(soup):
    """
    The first listed contact -- WA prints a contractual one, sometimes two.

    The page renders an empty table.contacts before the real one, and puts the
    Person/Phone/Email rows in a table nested inside it, so this looks for the
    first block that actually names someone rather than taking the first table
    it finds.
    """
    for table in soup.select("table.contacts"):
        contact = {}
        for row in table.select("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) != 2:
                continue
            label = cells[0].get_text(" ", strip=True)
            # The outer row is an icon cell plus everything else; its label is
            # empty, which is what rules it out.
            if not label or len(label) > 40:
                continue
            contact.setdefault(label, cells[1].get_text(" ", strip=True))
        if contact.get("Person"):
            return contact
    return {}


def parse_documents(soup):
    """
    The specification documents advertised on the page.

    Matched on the version stamp -- "(Ver 1 - 19 Feb, 2026)" -- rather than on
    the LIST_TITLE class alone, because the Status / Number / UNSPSC labels
    higher up the page carry that same class and would otherwise be scraped in
    as documents called "Status:".

    No URL is set: WA has none per document. See download_specifications.
    """
    documents = []
    for table in soup.select("table.nospace2"):
        for row in table.select("tr"):
            name = row.select_one("span.LIST_TITLE")
            if name is None:
                continue
            version = None
            for candidate in row.select("span.SMALL_GRAY"):
                match = VERSION_PATTERN.match(candidate.get_text(" ", strip=True))
                if match:
                    version = match.group(1).strip()
                    break
            if version is None:
                continue
            documents.append(
                Document(
                    file_name=sanitise_filename(name.get_text(strip=True)),
                    version=version,
                )
            )
    return documents


def parse_detail(html, url, base_url=BASE_URL):
    """Pull the structured fields off one tender-details page."""
    soup = BeautifulSoup(html or "", "html.parser")

    title = agency = None
    heading = soup.select_one("table.subtitle th.h2 div")
    if heading is not None:
        agency_span = heading.find("span")
        if agency_span is not None:
            match = ISSUED_BY_PATTERN.match(agency_span.get_text(" ", strip=True))
            agency = match.group(1).strip() if match else None
            agency_span.extract()
        title = heading.get_text(" ", strip=True) or None

    fields = parse_labelled_fields(soup)

    closing_date = None
    closes = soup.find(string=CLOSES_PATTERN)
    if closes:
        value = CLOSES_PATTERN.sub("", closes.strip())
        closing_date = TIMEZONE_SUFFIX.sub("", value).strip() or None

    contact = parse_contact(soup)
    specs_link = soup.select_one("a[href*='downloadspecs.action']")

    return {
        "title": title,
        "agency": agency,
        "reference": fields.get("Number"),
        "status_label": fields.get("Status"),
        "unspsc": fields.get("UNSPSC"),
        "regions": fields.get("Region/s"),
        "description": parse_description(soup),
        "closing_date": closing_date,
        "contact_name": contact.get("Person"),
        "contact_position": contact.get("Position"),
        "contact_phone": contact.get("Phone") or contact.get("Mobile"),
        "contact_email": contact.get("Email"),
        "documents": parse_documents(soup),
        "specs_url": urljoin(base_url, specs_link["href"]) if specs_link else None,
        "url": url,
    }


def format_detail(parsed, url):
    """Render the scraped fields as the tender's .txt file."""
    lines = [
        "=" * 80,
        f"TENDERS WA: {parsed.get('title') or parsed.get('reference') or 'Untitled'}",
        "=" * 80,
        "",
        f"Detail URL: {url}",
        f"Scraped At: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "STRUCTURED ATTRIBUTES:",
        "-" * 40,
    ]
    for key in (
        "reference",
        "agency",
        "status_label",
        "unspsc",
        "regions",
        "closing_date",
        "contact_name",
        "contact_position",
        "contact_phone",
        "contact_email",
    ):
        if parsed.get(key):
            lines.append(f"{key.replace('_', ' ').title()}: {parsed[key]}")

    if parsed.get("documents"):
        lines += ["", "SPECIFICATION DOCUMENTS:", "-" * 40]
        lines += [
            f"{document.file_name}" + (f" ({document.version})" if document.version else "")
            for document in parsed["documents"]
        ]

    if parsed.get("description"):
        lines += ["", "DESCRIPTION:", "-" * 40, parsed["description"]]

    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def _fail_all(documents, message):
    for document in documents:
        document.error = message


def extract_bundle(payload, folder, documents):
    """
    Unpack the specification zip into the tender's folder.

    Member names come from outside our control, so each one is reduced to a
    bare filename before it is written: an archive containing ../../evil.txt
    must land inside the folder or not at all. Nested folders are flattened for
    the same reason, which also happens to be what the document-extraction
    stage expects -- it looks for files directly in the tender directory.
    """
    folder = Path(folder)
    written = {}

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            name = sanitise_filename(member.filename.replace("\\", "/").split("/")[-1])
            target = unique_path(folder, name)
            try:
                with archive.open(member) as source, open(target, "wb") as handle:
                    shutil.copyfileobj(source, handle)
            except Exception as exc:
                log.warning("could not extract %s: %s", member.filename, exc)
                target.unlink(missing_ok=True)
                continue
            written[name.lower()] = target

    for document in documents:
        target = written.get(sanitise_filename(document.file_name).lower())
        if target is None:
            document.error = "advertised but not present in the specification bundle"
            continue
        document.downloaded = True
        document.local_path = target.name
        document.bytes_written = target.stat().st_size
        document.error = None

    log.info("unpacked %d file(s) from the specification bundle", len(written))
    return written


def download_specifications(client, specs_url, folder, documents, timeout=180.0):
    """
    Fetch the specification bundle and unpack it beside the tender's text.

    WA hands over every document in one request, so this is all-or-nothing:
    either the bundle arrives, or the tender's documents are recorded as
    needing a login. Returns True when the portal wanted one.

    Never raises. A tender whose documents cannot be fetched is still a tender
    worth having -- the names, versions and the reason are recorded either way.
    """
    if not specs_url:
        return False

    folder = Path(folder)
    try:
        response = client.get(
            specs_url, headers=HEADERS, timeout=timeout, follow_redirects=True
        )
    except Exception as exc:
        _fail_all(documents, f"{exc.__class__.__name__}: {exc}")
        return False

    if response.status_code != 200:
        _fail_all(documents, f"HTTP {response.status_code}")
        return False

    # A bundle is never HTML: the portal redirects an anonymous request to its
    # login form and answers 200 with a page.
    content_type = (response.headers.get("content-type") or "").lower()
    if "login" in str(response.url).lower() or "text/html" in content_type:
        log.info("specification documents need a registered session")
        return True

    payload = response.content
    if zipfile.is_zipfile(io.BytesIO(payload)):
        try:
            extract_bundle(payload, folder, documents)
        except Exception as exc:
            _fail_all(documents, f"could not unpack the bundle: {exc}")
        return False

    # A tender with a single specification is served as that file, not zipped.
    name = filename_from_response(
        response, documents[0].file_name if len(documents) == 1 else "specifications"
    )
    target = unique_path(folder, name)
    target.write_bytes(payload)

    if len(documents) == 1:
        documents[0].downloaded = True
        documents[0].local_path = target.name
        documents[0].bytes_written = len(payload)
        documents[0].error = None
    else:
        _fail_all(
            documents,
            f"the download was not a zip archive; saved as {target.name} instead",
        )
    return False


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def build_tender_record(parsed, entry, url, documents, requires_login):
    """
    Map the WA fields onto ingestion's 20-field shape.

    The listing entry is passed alongside the detail page because it carries
    the agency and the UNSPSC category as their own fields; where both have a
    value the detail page wins, since the listing truncates.
    """
    entry = entry or {}
    return build_record(
        source_id=SOURCE_ID,
        source_url=url,
        title=parsed.get("title") or entry.get("title"),
        reference=parsed.get("reference") or entry.get("reference"),
        issuing_agency=parsed.get("agency") or entry.get("agency"),
        category=classify_category(
            entry.get("type_label"), parsed.get("title"), entry.get("title")
        ),
        status=classify_status(parsed.get("status_label") or entry.get("status_label")),
        # WA prints no advertised date anywhere on the tender, and deriving one
        # from the closing date would silently misfile the tender in any
        # date-ordered view.
        publish_date=None,
        closing_date=parsed.get("closing_date") or entry.get("closing_date"),
        location=parsed.get("regions"),
        description=parsed.get("description"),
        contact_name=parsed.get("contact_name"),
        contact_email=parsed.get("contact_email")
        or first_email(parsed.get("description")),
        contact_phone=parsed.get("contact_phone"),
        documents=documents,
        requires_login=requires_login,
        raw_extra={
            "tender_type": entry.get("type_label"),
            "unspsc_category": parsed.get("unspsc") or entry.get("category"),
            "regions": parsed.get("regions"),
            "contact_position": parsed.get("contact_position"),
            "specifications_url": parsed.get("specs_url"),
            "documents_advertised_on_listing": entry.get("has_documents"),
        },
    )


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def scrape_tender(client, entry, output_dir=None, pause=0.3):
    """
    Scrape one tender into its own directory. Returns the record written.

    `entry` is the listing row, which carries fields the detail page does not.
    """
    url = entry["url"]
    response = client.get(url, headers=HEADERS, timeout=30.0)
    response.raise_for_status()
    parsed = parse_detail(response.text, url)

    reference = parsed.get("reference") or entry.get("reference") or entry["tender_id"]
    folder = tender_dir(reference, output_dir)
    write_tender_text(folder, format_detail(parsed, url))

    documents = parsed["documents"]
    requires_login = download_specifications(
        client, parsed["specs_url"], folder, documents
    )
    for document in documents:
        if document.error:
            log.warning("  %s: %s", document.file_name, document.error)
    time.sleep(pause)

    record = build_tender_record(parsed, entry, url, documents, requires_login)
    write_tender_record(folder, record)

    downloaded = sum(1 for document in documents if document.downloaded)
    log.info(
        "%s -> %s (%d/%d document(s))",
        reference,
        folder.name,
        downloaded,
        len(documents),
    )
    return record


def run_scraper(limit=10, output_dir=None, pause=0.5):
    """
    Scrape the first `limit` advertised WA tenders into one directory each.

    `limit` of 0 means every tender -- the search returns them all on one page,
    so there is no pagination cost to that. Individual tenders that fail are
    logged and skipped so one bad page cannot end the run.
    """
    records = []

    with httpx.Client(follow_redirects=True) as client:
        if apply_session_cookie(client):
            log.info("using the session cookie from WA_TENDERS_COOKIE")

        nonce = open_session(client)
        if nonce is None:
            log.warning("no CSRF nonce on the home page -- continuing without one")

        username, password = credentials()
        if username and password and not os.environ.get("WA_TENDERS_COOKIE"):
            log_in(client, username, password)
        elif not os.environ.get("WA_TENDERS_COOKIE"):
            log.warning(
                "no WA_TENDERS_COOKIE or WA_TENDERS_USERNAME/PASSWORD set -- tender "
                "text will be scraped but specification documents need a login"
            )

        listing = client.get(search_url(nonce), headers=HEADERS, timeout=45.0)
        if listing.status_code != 200:
            log.error("tender search returned HTTP %s", listing.status_code)
            return records

        entries = parse_listing(listing.text)
        log.info("%d advertised tender(s) on the search page", len(entries))
        if limit:
            entries = entries[:limit]

        for entry in entries:
            try:
                records.append(scrape_tender(client, entry, output_dir, pause))
            except Exception as exc:
                log.error("%s failed: %s", entry.get("reference") or entry["url"], exc)

    log.info("scraped %d tender(s)", len(records))
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("LIMIT", "10")),
        help="max tenders to scrape (0 = every advertised tender)",
    )
    parser.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR"))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run_scraper(limit=args.limit, output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
