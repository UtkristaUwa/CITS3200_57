"""
error_scrapers/nt_qtol/scraper.py

Scraper for NT Quotations and Tenders Online (tendersonline.nt.gov.au).
Same shape as error_scrapers/grant_connect/scraper.py: every function here
is covered by test_cases/test_nt_qtol.py.

Per tender, saves into its own folder (via common.py's
tender_dir/save_page_text/unpack_zip):
    __tender__<reference>.txt -- text from the detail page's own fields
    <attachment files>        -- the files from the tender's document bundle
    <attachment>.txt          -- extracted text per attachment

Plain HTTP, no browser: the search results come from a server-rendered
fragment endpoint that the page's own JavaScript calls, so we ask it directly.

Documents:
    QTOL names no documents on the page. One "Download tender" button hands
    over the whole profile as a zip, and an anonymous request is redirected to
    the login form -- the account also needs a registered business attached.
    Set NT_QTOL_COOKIE to a signed-in browser's session cookie
    (".AspNet.ApplicationCookie=...") to download them. Without it every
    tender is TENDER_PARTIAL (page text only); with it, a download that still
    lands on the login form means the cookie is stale: SITE_LOGIN_FAILED.
"""

import math
import os
import re
import tempfile
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from error_scrapers import common

from dotenv import load_dotenv
load_dotenv()

SOURCE_ID = "nt-qtol"
BASE_URL = "https://tendersonline.nt.gov.au"
LIST_URL = f"{BASE_URL}/Tender/SearchResults/Current"
DETAIL_PATH = "/Tender/Details"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    # The search endpoint is what the page's own JavaScript calls.
    "X-Requested-With": "XMLHttpRequest",
}

# The page-size select offers 5, 10, 15 and 20. Ask for anything else and
# QTOL silently falls back to 5 -- a run that believed it had every tender
# would quietly have taken a quarter of them.
PAGE_SIZE = 20

# Detail links on a listing card.
DETAIL_LINK_SELECTOR = ".tender-card__title a[href*='/Tender/Details/']"

# The Release / Close / Award milestone cells every detail page carries. If
# these are missing on an otherwise real page, the site's structure has
# changed -- the structure-changed fixture renames exactly this class.
FIELD_SELECTOR = ".stepper-info-cell"

DOWNLOAD_LINK_SELECTOR = "a[href*='/Tender/DownloadProfile/']"

TENDER_ID_PATTERN = re.compile(rf"{DETAIL_PATH}/(\d+)")
REFERENCE_PATTERN = re.compile(r"Tender number\s+(\S+)", re.I)
REGION_PATTERN = re.compile(r"following region\(s\)", re.I)
PHONE_PATTERN = re.compile(r"(?:\+61\s*)?(?:\(0\d\)|0\d)[\d\s]{6,}")
# "To be determined", "TBA", "-" sit in the same cell shape as a real date.
NOT_A_DATE = re.compile(r"^\s*(to be determined|tbd|tba|n/?a|-|)\s*$", re.I)

PAUSE_SECONDS = 0.3


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def apply_session_cookie(client) -> bool:
    """Put NT_QTOL_COOKIE on the client. Returns True if one was set."""
    applied = False
    for part in os.environ.get("NT_QTOL_COOKIE", "").split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            client.cookies.set(name.strip(), value.strip(), domain="tendersonline.nt.gov.au")
            applied = True
    return applied


def is_login_redirect(response) -> bool:
    """True if QTOL answered with its login form instead of what was asked for."""
    content_type = (response.headers.get("content-type") or "").lower()
    return "/account/logon" in str(response.url).lower() or "text/html" in content_type


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def detail_url(tender_id: str) -> str:
    """
    A stable URL for one tender. The listing's own links append
    ?status=Current, which would change source_url the day the tender closes.
    """
    return f"{BASE_URL}{DETAIL_PATH}/{tender_id}"


def parse_listing(html: str) -> list[str]:
    """Return every tender detail-page URL on one search-results fragment."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.select(DETAIL_LINK_SELECTOR):
        match = TENDER_ID_PATTERN.search(anchor.get("href") or "")
        if match:
            url = detail_url(match.group(1))
            if url not in links:
                links.append(url)
    return links


def total_pages(html: str) -> int | None:
    """
    How many pages the search says it has, or None when it does not say --
    in which case the caller keeps going until a page comes back empty.
    """
    results = BeautifulSoup(html, "html.parser").select_one("#tender-search-results")
    value = (results.get("data-total-records") or "").strip() if results else ""
    return math.ceil(int(value) / PAGE_SIZE) if value.isdigit() else None


def _text(soup, selector):
    found = soup.select_one(selector)
    return found.get_text(" ", strip=True) if found else None


def _block_after(soup, heading):
    """The lines of the block introduced by `heading`, minus the heading itself."""
    label = soup.find(string=re.compile(rf"^\s*{heading}\s*$", re.I))
    if label is None or label.parent is None or label.parent.parent is None:
        return []
    return [
        line
        for line in label.parent.parent.get_text("\n", strip=True).split("\n")
        if line and not re.match(rf"^\s*{heading}\s*$", line, re.I)
    ]


def _region(soup):
    """The region(s) the works cover, from the sentence that introduces them."""
    intro = soup.find(string=REGION_PATTERN)
    if intro is None or intro.parent is None or intro.parent.parent is None:
        return None
    lines = [
        line for line in intro.parent.parent.get_text("\n", strip=True).split("\n")
        if line and not REGION_PATTERN.search(line)
    ]
    return ", ".join(lines) or None


def parse_detail(html: str) -> tuple[dict, int]:
    """
    Pull a tender's own fields off its detail page.

    Returns (fields, status_code). If the milestone cells or the tender
    number aren't found on an otherwise real page, returns
    ({}, SITE_STRUCTURE_CHANGE) rather than guessing at a different layout.
    """
    soup = BeautifulSoup(html, "html.parser")

    cells = soup.select(FIELD_SELECTOR)
    reference = REFERENCE_PATTERN.search(_text(soup, "p.leader") or "")
    if not cells or reference is None:
        return {}, common.SITE_STRUCTURE_CHANGE

    milestones = {}
    for cell in cells:
        paragraphs = cell.find_all("p")
        if len(paragraphs) >= 2:
            label = paragraphs[0].get_text(" ", strip=True).rstrip(":")
            value = paragraphs[1].get_text(" ", strip=True)
            milestones[label] = None if NOT_A_DATE.match(value) else value

    # The Category / Procurement method grid: label cell, value cell, repeat.
    grid = {}
    for row in soup.select("div.row.row-cols-2"):
        cells_text = [c.get_text(" ", strip=True) for c in row.find_all("div", recursive=False)]
        for label, value in zip(cells_text[::2], cells_text[1::2]):
            if label.endswith(":"):
                grid[label.rstrip(":")] = value or None

    listed_by = _block_after(soup, "Listed by")

    phone = None
    enquiries = soup.find(string=re.compile(r"enquiries", re.I))
    if enquiries is not None and enquiries.parent is not None and enquiries.parent.parent is not None:
        match = PHONE_PATTERN.search(enquiries.parent.parent.get_text(" ", strip=True))
        phone = match.group().strip() if match else None

    fields = {
        "title": _text(soup, "#tenderTitle h1"),
        "reference": reference.group(1),
        "status": _text(soup, ".general-badge.green-badge, p.leader ~ .general-badge"),
        "agency": listed_by[0] if listed_by else None,
        "release_date": milestones.get("Release"),
        "close_date": milestones.get("Close"),
        "award_date": milestones.get("Award"),
        "category": grid.get("Category"),
        "procurement_method": grid.get("Procurement method"),
        "region": _region(soup),
        "enquiries_phone": phone,
        "lodgment_address": ", ".join(listed_by[1:]) or None,
        "lodgement_details": " ".join(_block_after(soup, "Lodgement details")) or None,
        "description": _text(soup, "#tenderDescription"),
    }
    return fields, common.SITE_SUCCESS


def find_download_url(html: str) -> str | None:
    """The "Download tender" link, or None if the page offers no download."""
    anchor = BeautifulSoup(html, "html.parser").select_one(DOWNLOAD_LINK_SELECTOR)
    if anchor is None or not anchor.get("href"):
        return None
    return urljoin(BASE_URL, anchor["href"])


def format_detail_text(fields: dict, url: str) -> str:
    """Render the parsed fields as the tender's page-text file."""
    lines = [
        "=" * 80,
        f"NT QUOTATIONS AND TENDERS ONLINE: {fields.get('title') or fields.get('reference')}",
        "=" * 80,
        "",
        f"Detail URL: {url}",
        "",
    ]
    for key, value in fields.items():
        if value and key != "description":
            lines.append(f"{key}: {value}")
    if fields.get("description"):
        lines += ["", "DESCRIPTION:", "-" * 40, fields["description"]]
    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Downloading + extraction
# ---------------------------------------------------------------------------

def download_bundle(client, download_url: str, folder: str) -> tuple[int, list[dict], bool]:
    """
    Download the tender's document zip and unpack it into the folder.

    Returns (status_code, attachments, gated). `gated` means QTOL sent the
    login form instead of the zip. The zip itself is streamed to a temporary
    file and deleted once unpacked -- it isn't an attachment, its contents are.
    """
    zip_path = None
    try:
        with client.stream("GET", download_url, headers=HEADERS, timeout=180.0) as response:
            response.raise_for_status()
            if is_login_redirect(response):
                return common.TENDER_PARTIAL, [], True
            with tempfile.NamedTemporaryFile(dir=folder, suffix=".zip", delete=False) as handle:
                zip_path = handle.name
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        attachments, any_failed = common.unpack_zip(zip_path, folder)
    finally:
        # Also on a failed download, so a half-written zip never sits in the
        # folder looking like an attachment.
        if zip_path and os.path.exists(zip_path):
            os.remove(zip_path)

    code = common.TENDER_PARTIAL if any_failed else common.SITE_SUCCESS
    return code, attachments, False


# ---------------------------------------------------------------------------
# Per-tender and full-run orchestration
# ---------------------------------------------------------------------------

def scrape_opportunity(client, url: str, output_dir: str = "tenders_data") -> tuple[int, dict]:
    """
    Scrape one tender into its own folder.

    Returns (status_code, tender). `tender` is empty if the page could not be
    parsed. `documents_gated` on the tender records that the download was
    refused for want of a login.
    """
    response = client.get(url, headers=HEADERS, timeout=30.0)
    response.raise_for_status()
    fields, code = parse_detail(response.text)
    if code != common.SITE_SUCCESS:
        return code, {}

    reference = fields["reference"]
    folder = common.tender_dir(reference, output_dir)
    common.save_page_text(folder, reference, format_detail_text(fields, url))

    tender = {
        "tender_id": reference,
        "title": fields.get("title"),
        "folder": folder,
        "attachments": [],
        "source_url": url,
        "documents_gated": False,
    }

    download_url = find_download_url(response.text)
    if download_url is None:
        return common.SITE_SUCCESS, tender

    try:
        code, tender["attachments"], tender["documents_gated"] = download_bundle(
            client, download_url, folder
        )
    except Exception as e:
        print(f"NT QTOL DOWNLOAD FAILED: {download_url}: {e}")
        code = common.TENDER_PARTIAL
    return code, tender


def collect_all_listing_urls(client, limit: int = 0) -> list[str]:
    """
    Walk the search pages, stopping at the page count the first page reports,
    at an empty page, or once `limit` is reached. `limit` of 0 means every
    current tender.

    Raises StructureChangedError if the very first page has no tender cards:
    QTOL always has current tenders, so an empty first page means the markup
    changed, not that there is nothing to scrape.
    """
    urls, page, pages = [], 1, None
    while True:
        response = client.get(
            LIST_URL, params={"page": page, "size": PAGE_SIZE}, headers=HEADERS, timeout=45.0
        )
        response.raise_for_status()
        if pages is None:
            pages = total_pages(response.text)

        page_urls = [u for u in parse_listing(response.text) if u not in urls]
        if not page_urls:
            if page == 1:
                raise common.StructureChangedError("No tender cards on the first search page.")
            break
        urls.extend(page_urls)
        if limit and len(urls) >= limit:
            return urls[:limit]
        if pages is not None and page >= pages:
            break
        page += 1
        time.sleep(PAUSE_SECONDS)
    return urls


def run_scraper(limit: int = 0, output_dir: str = "tenders_data") -> tuple[int, list[dict]]:
    """
    Scrape every current NT tender. `limit` of 0 means every tender found.

    Returns (site_code, tenders). Downloads refused for want of a login are
    SITE_LOGIN_FAILED when NT_QTOL_COOKIE was set (the cookie is stale) and
    TENDER_PARTIAL when it wasn't.
    """
    tenders: list[dict] = []
    try:
        with httpx.Client(follow_redirects=True) as client:
            have_cookie = apply_session_cookie(client)
            if not have_cookie:
                print("NT_QTOL_COOKIE not set -- tender documents will be skipped")

            urls = collect_all_listing_urls(client, limit)

            tender_codes = set()
            for url in urls:
                try:
                    code, tender = scrape_opportunity(client, url, output_dir)
                    if tender:
                        tenders.append(tender)
                        if tender["documents_gated"] and have_cookie:
                            code = common.SITE_LOGIN_FAILED
                    tender_codes.add(code)
                except Exception as e:
                    print(f"NT QTOL TENDER FAILED: {url}: {e}")
                    tender_codes.add(common.TENDER_PARTIAL)
                time.sleep(PAUSE_SECONDS)

        return common.site_code_from(tender_codes), tenders

    except common.StructureChangedError:
        return common.SITE_STRUCTURE_CHANGE, tenders
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            return common.SITE_RATE_LIMITED, tenders
        return common.SITE_TOTAL_FAILURE, tenders
    except httpx.TransportError:
        return common.SITE_TOTAL_FAILURE, tenders


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scrape current NT QTOL tenders.")
    parser.add_argument("--limit", type=int, default=5, help="max tenders (0 = all)")
    parser.add_argument("--output-dir", default="tenders_data")
    args = parser.parse_args()

    code, tenders = run_scraper(limit=args.limit, output_dir=args.output_dir)
    print(f"NT QTOL run finished with code {code}, {len(tenders)} tenders scraped")


if __name__ == "__main__":
    main()
