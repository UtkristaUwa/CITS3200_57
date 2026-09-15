"""
Web scraper for Quotations and Tenders Online (https://tendersonline.nt.gov.au)
-- NT government agencies, and the councils that list through them.

Output: one directory per tender under tenders_data/, containing the scraped
detail text as <REFERENCE>.txt, a tender.json ingestion record, and any
documents the session was allowed to download.

Plain HTTP, no browser and no account needed to read: the search results are
rendered server-side and served from a fragment endpoint that the page's own
JavaScript calls, so this asks that endpoint directly and pages through it.

Documents:
    QTOL advertises no filenames. One "Download tender" button hands over the
    whole profile, and an anonymous request is redirected to the login form --
    the account also needs a registered business attached to it. So the honest
    output for an anonymous run is an empty document list *with*
    documents_require_login recorded, which is what tells a consumer this is
    not a tender that simply has no attachments.

Login:
    NT_QTOL_COOKIE   a session cookie from a signed-in browser, e.g.
                     ".AspNet.ApplicationCookie=..." -- applied to the session
                     if set. Without it the scrape still runs and still writes
                     one folder per tender.

Usage:
    python -m web_scrapers.nt_qtol.nt_qtol --limit 10
"""

import argparse
import io
import logging
import math
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

SOURCE_ID = "nt-qtol"
BASE_URL = "https://tendersonline.nt.gov.au"
SEARCH_PATH = "/Tender/SearchResults/Current"
DETAIL_PATH = "/Tender/Details"

# The page-size select offers exactly these. Ask for anything else -- 36, 100 --
# and QTOL silently falls back to 5, so a run that believed it had every tender
# would quietly have taken a fifth of them.
ALLOWED_PAGE_SIZES = (5, 10, 15, 20)
PAGE_SIZE = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    # The endpoint is what the page's own JavaScript calls.
    "X-Requested-With": "XMLHttpRequest",
}

TENDER_ID_PATTERN = re.compile(rf"{DETAIL_PATH}/(\d+)")
CLOSING_PREFIX = re.compile(r"^\s*Clos(?:ing|es)\s+", re.I)
REGION_PATTERN = re.compile(r"following region\(s\)", re.I)
PHONE_PATTERN = re.compile(r"(?:\+61\s*)?(?:\(0\d\)|0\d)[\d\s]{6,}")

# "To be determined", "TBA", "-" all sit in the same cell shape as a real date.
NOT_A_DATE = re.compile(r"^\s*(to be determined|tbd|tba|n/?a|-|)\s*$", re.I)


# ---------------------------------------------------------------------------
# Session and search
# ---------------------------------------------------------------------------

def apply_session_cookie(client):
    """Put NT_QTOL_COOKIE on the client, if one is configured."""
    raw = os.environ.get("NT_QTOL_COOKIE", "").strip()
    if not raw:
        return False

    applied = False
    for part in raw.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            client.cookies.set(name.strip(), value.strip(), domain="tendersonline.nt.gov.au")
            applied = True

    if not applied:
        log.warning("NT_QTOL_COOKIE is set but is not a name=value pair -- ignoring")
    return applied


def search_url(page=1, size=PAGE_SIZE):
    """One page of the current-tender search."""
    if size not in ALLOWED_PAGE_SIZES:
        log.debug("page size %s is not one QTOL honours -- using %s", size, PAGE_SIZE)
        size = PAGE_SIZE
    return f"{BASE_URL}{SEARCH_PATH}?page={page}&size={size}"


def detail_url(tender_id):
    """
    A stable URL for one tender.

    The listing's own links append ?status=Current, which is the view the user
    came from rather than part of the tender's identity -- keeping it would
    change source_url the day the tender closes.
    """
    return f"{BASE_URL}{DETAIL_PATH}/{tender_id}"


def total_records(html):
    """
    How many tenders the search says it has, or None when it does not say.

    None rather than 0: zero would end the paging loop immediately, while None
    means "keep asking until a page comes back empty".
    """
    results = BeautifulSoup(html or "", "html.parser").select_one("#tender-search-results")
    if results is None:
        return None
    value = (results.get("data-total-records") or "").strip()
    return int(value) if value.isdigit() else None


def page_count(total, size=PAGE_SIZE):
    """How many pages that many records needs, or None if the count is unknown."""
    if total is None:
        return None
    return math.ceil(total / size)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _text(node, selector):
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if found else None


def parse_listing(html, base_url=BASE_URL):
    """
    Return one entry per tender card in a search-results fragment.

    The card carries the full description as well as the summary fields, so a
    tender remains usable even if its detail page fails -- the ingestion
    record still has something to say about it.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    entries = []
    for card in soup.select(".tender-card"):
        link = card.select_one("h3.tender-card__title a, .tender-card__title a")
        if link is None:
            continue
        identifier = TENDER_ID_PATTERN.search(link.get("href") or "")
        if identifier is None:
            continue

        # QTOL prints the request type and the reference as one string:
        # "Quote T26-1279", "Tender NTG26-0168".
        number = _text(card, "p.tender-card__tender-number") or ""
        type_label, _, reference = number.partition(" ")

        closing = _text(card, "p.tender-card__closing-date")
        if closing:
            closing = CLOSING_PREFIX.sub("", closing).strip() or None

        badges = [b.get_text(" ", strip=True) for b in card.select(".general-badge")]
        status_label = _text(card, ".general-badge.green-badge")
        category = next((b for b in badges if b and b != status_label), None)

        entries.append(
            {
                "tender_id": identifier.group(1),
                "url": detail_url(identifier.group(1)),
                "reference": reference.strip() or None,
                "type_label": type_label.strip() or None,
                "title": link.get_text(" ", strip=True) or None,
                "agency": _text(card, "p.tender-card__agency-text"),
                "description": _text(card, ".tender-card__description-text"),
                "category": category,
                "closing_date": closing,
                "status_label": status_label,
            }
        )
    return entries


def parse_milestones(soup):
    """The Release / Close / Award cells, as a dict of what was actually printed."""
    milestones = {}
    for cell in soup.select(".stepper-info-cell"):
        paragraphs = cell.find_all("p")
        if len(paragraphs) < 2:
            continue
        label = paragraphs[0].get_text(" ", strip=True).rstrip(":")
        value = paragraphs[1].get_text(" ", strip=True)
        # "To be determined" sits in the same cell shape as a real date, and
        # the date parser would return None for it anyway -- but recording it
        # as absent here keeps the intent visible.
        milestones[label] = None if NOT_A_DATE.match(value) else value
    return milestones


def parse_labelled_fields(soup):
    """The Category / Procurement method grid, as a dict."""
    fields = {}
    for row in soup.select("div.row.row-cols-2"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("div", recursive=False)]
        for label, value in zip(cells[::2], cells[1::2]):
            if label.endswith(":"):
                fields[label.rstrip(":")] = value or None
    return fields


def parse_region(soup):
    """The region(s) the works cover, from the sentence that introduces them."""
    intro = soup.find(string=REGION_PATTERN)
    if intro is None:
        return None
    container = intro.parent.parent if intro.parent else None
    if container is None:
        return None
    text = container.get_text("\n", strip=True)
    lines = [line for line in text.split("\n") if line and not REGION_PATTERN.search(line)]
    return ", ".join(lines) or None


def _block_after(soup, heading):
    """The text of the block introduced by `heading`, minus the heading itself."""
    label = soup.find(string=re.compile(rf"^\s*{heading}\s*$", re.I))
    if label is None or label.parent is None or label.parent.parent is None:
        return None
    lines = [
        line
        for line in label.parent.parent.get_text("\n", strip=True).split("\n")
        if line and not re.match(rf"^\s*{heading}\s*$", line, re.I)
    ]
    return lines or None


def parse_detail(html, url):
    """Pull the structured fields off one tender-details page."""
    soup = BeautifulSoup(html or "", "html.parser")

    milestones = parse_milestones(soup)
    fields = parse_labelled_fields(soup)

    listed_by = _block_after(soup, "Listed by") or []
    lodgement = _block_after(soup, "Lodgement details") or []

    phone = None
    enquiries = soup.find(string=re.compile(r"enquiries", re.I))
    if enquiries is not None and enquiries.parent is not None:
        match = PHONE_PATTERN.search(enquiries.parent.parent.get_text(" ", strip=True))
        phone = match.group().strip() if match else None

    download = soup.select_one("a[href*='DownloadProfile']")

    return {
        "url": url,
        "release_date": milestones.get("Release"),
        "closing_date": milestones.get("Close"),
        "award_date": milestones.get("Award"),
        "category": fields.get("Category"),
        "procurement_method": fields.get("Procurement method"),
        "region": parse_region(soup),
        "agency": listed_by[0] if listed_by else None,
        "lodgment_address": ", ".join(listed_by[1:]) or None,
        "lodgement_details": " ".join(lodgement) or None,
        "contact_phone": phone,
        "download_url": urljoin(BASE_URL, download["href"]) if download else None,
    }


def format_detail(parsed, entry, url):
    """Render the scraped fields as the tender's .txt file."""
    lines = [
        "=" * 80,
        f"NT QUOTATIONS AND TENDERS ONLINE: {entry.get('title') or entry.get('reference')}",
        "=" * 80,
        "",
        f"Detail URL: {url}",
        f"Scraped At: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "STRUCTURED ATTRIBUTES:",
        "-" * 40,
    ]
    printable = [
        ("Reference", entry.get("reference")),
        ("Request Type", entry.get("type_label")),
        ("Agency", parsed.get("agency") or entry.get("agency")),
        ("Status", entry.get("status_label")),
        ("Category", parsed.get("category") or entry.get("category")),
        ("Procurement Method", parsed.get("procurement_method")),
        ("Region Of Works", parsed.get("region")),
        ("Release Date", parsed.get("release_date")),
        ("Closing Date", parsed.get("closing_date") or entry.get("closing_date")),
        ("Award Date", parsed.get("award_date")),
        ("Enquiries Phone", parsed.get("contact_phone")),
        ("Listed By Address", parsed.get("lodgment_address")),
    ]
    lines += [f"{label}: {value}" for label, value in printable if value]

    if parsed.get("lodgement_details"):
        lines += ["", "LODGEMENT DETAILS:", "-" * 40, parsed["lodgement_details"]]
    if entry.get("description"):
        lines += ["", "DESCRIPTION:", "-" * 40, entry["description"]]

    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def collect_documents(client, download_url, folder, timeout=180.0):
    """
    Fetch the tender profile and unpack it, returning (documents, requires_login).

    QTOL names no documents on the page, so the list is built from what the
    archive actually contained -- there is nothing to reconcile it against.
    An anonymous request is redirected to the login form, which is reported as
    requires_login rather than as an error: it is the expected outcome when no
    session cookie is configured.

    Never raises. A tender whose documents cannot be fetched is still worth
    recording.
    """
    if not download_url:
        return [], False

    folder = Path(folder)
    try:
        response = client.get(
            download_url, headers=HEADERS, timeout=timeout, follow_redirects=True
        )
    except Exception as exc:
        log.warning("could not fetch %s: %s", download_url, exc)
        return [], False

    if response.status_code != 200:
        log.warning("download returned HTTP %s", response.status_code)
        return [], False

    content_type = (response.headers.get("content-type") or "").lower()
    if "logon" in str(response.url).lower() or "text/html" in content_type:
        log.info("tender documents need a signed-in account with a business attached")
        return [], True

    payload = response.content
    if not zipfile.is_zipfile(io.BytesIO(payload)):
        log.warning("the tender profile was not a zip archive -- keeping it as-is")
        target = unique_path(folder, "tender-profile.bin")
        target.write_bytes(payload)
        return [
            Document(
                file_name=target.name,
                downloaded=True,
                local_path=target.name,
                bytes_written=len(payload),
            )
        ], False

    documents = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            # Member names come from outside our control: reduce each to a bare
            # filename so an archive containing ../../evil.txt lands inside the
            # tender folder or not at all.
            name = sanitise_filename(member.filename.replace("\\", "/").split("/")[-1])
            target = unique_path(folder, name)
            document = Document(file_name=name)
            try:
                with archive.open(member) as source, open(target, "wb") as handle:
                    shutil.copyfileobj(source, handle)
                document.downloaded = True
                document.local_path = target.name
                document.bytes_written = target.stat().st_size
            except Exception as exc:
                target.unlink(missing_ok=True)
                document.error = f"{exc.__class__.__name__}: {exc}"
            documents.append(document)

    log.info("unpacked %d file(s) from the tender profile", len(documents))
    return documents, False


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

# QTOL prints the request type as a single controlled word. "Quote" is an RFQ,
# but the shared classifier only recognises the longer wordings ("request for
# quotation", "RFQ") that the other portals use -- and widening it there would
# start turning any tender whose *title* mentions a quote into an RFQ. Mapping
# the portal's own vocabulary here keeps that precision local to the portal
# that needs it.
REQUEST_TYPES = {
    "quote": "rfq",
    "quotation": "rfq",
    "tender": "tender",
    "eoi": "eoi",
    "expression of interest": "eoi",
    "grant": "grant",
}


def classify_request_type(type_label, title=None):
    """QTOL's own request-type word, falling back to the shared classifier."""
    mapped = REQUEST_TYPES.get((type_label or "").strip().lower())
    return mapped or classify_category(type_label, title)


def build_tender_record(parsed, entry, url, documents, requires_login):
    """Map the QTOL fields onto ingestion's 20-field shape."""
    entry = entry or {}
    return build_record(
        source_id=SOURCE_ID,
        source_url=url,
        title=entry.get("title"),
        reference=entry.get("reference"),
        issuing_agency=parsed.get("agency") or entry.get("agency"),
        category=classify_request_type(entry.get("type_label"), entry.get("title")),
        status=classify_status(entry.get("status_label")),
        publish_date=parsed.get("release_date"),
        closing_date=parsed.get("closing_date") or entry.get("closing_date"),
        location=parsed.get("region"),
        description=entry.get("description"),
        contact_phone=parsed.get("contact_phone"),
        contact_email=first_email(parsed.get("lodgement_details"), entry.get("description")),
        lodgment_address=parsed.get("lodgment_address"),
        documents=documents,
        requires_login=requires_login,
        raw_extra={
            "request_type": entry.get("type_label"),
            "category_label": parsed.get("category") or entry.get("category"),
            "procurement_method": parsed.get("procurement_method"),
            "region_of_works": parsed.get("region"),
            "lodgement_details": parsed.get("lodgement_details"),
            "award_date": parsed.get("award_date"),
            "download_url": parsed.get("download_url"),
        },
    )


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def scrape_tender(client, entry, output_dir=None, pause=0.3):
    """Scrape one tender into its own directory. Returns the record written."""
    url = entry["url"]
    response = client.get(url, headers=HEADERS, timeout=30.0)
    response.raise_for_status()
    parsed = parse_detail(response.text, url)

    reference = entry.get("reference") or entry["tender_id"]
    folder = tender_dir(reference, output_dir)
    write_tender_text(folder, format_detail(parsed, entry, url))

    documents, requires_login = collect_documents(
        client, parsed["download_url"], folder
    )
    time.sleep(pause)

    record = build_tender_record(parsed, entry, url, documents, requires_login)
    write_tender_record(folder, record)

    downloaded = sum(1 for document in documents if document.downloaded)
    log.info("%s -> %s (%d document(s))", reference, folder.name, downloaded)
    return record


def collect_entries(client, limit, size=PAGE_SIZE):
    """
    Walk the search pages until `limit` tenders are in hand, or they run out.

    `limit` of 0 means every current tender. The record count the first page
    reports bounds the walk; if the portal stops reporting it, an empty page
    ends the loop instead.
    """
    entries, page, pages = [], 1, None
    while True:
        response = client.get(search_url(page, size), headers=HEADERS, timeout=45.0)
        if response.status_code != 200:
            log.error("search page %d returned HTTP %s", page, response.status_code)
            break

        if pages is None:
            pages = page_count(total_records(response.text), size)
            if pages is not None:
                log.info("%d page(s) of current tenders", pages)

        found = parse_listing(response.text)
        if not found:
            break
        entries.extend(found)

        if limit and len(entries) >= limit:
            break
        if pages is not None and page >= pages:
            break
        page += 1

    return entries[:limit] if limit else entries


def run_scraper(limit=10, output_dir=None, pause=0.5):
    """
    Scrape the first `limit` current NT tenders into one directory each.

    `limit` of 0 means every current tender. Individual tenders that fail are
    logged and skipped so one bad page cannot end the run.
    """
    records = []

    with httpx.Client(follow_redirects=True) as client:
        if apply_session_cookie(client):
            log.info("using the session cookie from NT_QTOL_COOKIE")
        else:
            log.warning(
                "no NT_QTOL_COOKIE set -- tender text will be scraped but the "
                "documents need a signed-in account with a business attached"
            )

        entries = collect_entries(client, limit)
        log.info("%d tender(s) to scrape", len(entries))

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
        help="max tenders to scrape (0 = every current tender)",
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
