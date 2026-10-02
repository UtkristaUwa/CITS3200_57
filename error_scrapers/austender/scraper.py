"""
error_scrapers/austender/scraper.py

Scraper for AusTender (tenders.gov.au) -- federal Approaches to Market.
Same shape as error_scrapers/grant_connect/scraper.py: every function here
is covered by test_cases/test_austender.py.

Per ATM, saves into its own folder (via common.py's
tender_dir/save_page_text/save_attachment_stream/save_extracted_text):
    __tender__<ATM ID>.txt -- text from the detail page's own fields
    <attachment files>     -- raw downloads, as-is
    <attachment>.txt       -- extracted text per attachment

Login:
    The detail page is public, but its "ATM Documents" page redirects an
    anonymous visitor to the login form. Set AUSTENDER_USERNAME /
    AUSTENDER_PASSWORD to download attachments. Without them the page text
    is still scraped and every tender with documents is reported as
    TENDER_PARTIAL; with them, a documents page that still redirects to the
    login form means the login did not take, and the run reports
    SITE_LOGIN_FAILED.

    A successful login does not announce itself: AusTender answers the login
    POST with the login page again, so whether a documents page actually
    resolves is the only reliable check -- and it is the one used here.
"""

import os
import re
import time
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from error_scrapers import common

from dotenv import load_dotenv
load_dotenv()

SOURCE_ID = "austender"
BASE_URL = "https://www.tenders.gov.au"
LOGIN_URL = f"{BASE_URL}/RegisteredUser/Login"
LIST_URL = f"{BASE_URL}/atm"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Detail page links, from the public listing.
DETAIL_LINK_SELECTOR = "a[href*='/Atm/Show/']"

# The container each detail field (ATM ID, Agency, Close Date & Time, ...)
# lives in. If this finds nothing on an otherwise real page, the site's
# structure has changed -- the structure-changed fixture renames exactly this
# class to simulate that.
FIELD_SELECTOR = "div.list-desc"

# The "ATM Documents" button on a detail page.
DOCUMENTS_PAGE_SELECTOR = "a[href*='/Atm/ViewDocuments/']"

# Both attachment routes on the documents page. Selected on the route rather
# than on link text, which varies ("Download", the filename, an icon only).
DOCUMENT_LINK_SELECTOR = (
    "a[href*='/Atm/DownloadSoftCopy/'], a[href*='/Atm/DownloadAddenda/']"
)

# Pause between requests, so a full run doesn't hammer the portal.
PAUSE_SECONDS = 0.3


def credentials():
    """Return (username, password) from the environment, or (None, None)."""
    return (
        os.environ.get("AUSTENDER_USERNAME") or None,
        os.environ.get("AUSTENDER_PASSWORD") or None,
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

# Every AusTender page carries a login form in its header, so "has a password
# box" can't tell the login page apart from anything else. The full-page login
# form is the only one whose ReturnUrl input has this id.
LOGIN_PAGE_MARKER = "input#form-ReturnUrl"


def is_login_page(html: str) -> bool:
    """True if AusTender served its login page instead of the page asked for."""
    return BeautifulSoup(html, "html.parser").select_one(LOGIN_PAGE_MARKER) is not None


def login(client) -> bool:
    """
    Submit the registered-user login form.

    The form carries an anti-forgery token, so it is read back and every
    hidden input replayed. Returns True if the form was found and posted
    without an HTTP error -- see the module docstring for why the response
    body can't tell us more than that.
    """
    username, password = credentials()
    response = client.get(LOGIN_URL, headers=HEADERS, timeout=20.0)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    marker = soup.select_one(LOGIN_PAGE_MARKER)
    form = marker.find_parent("form") if marker else soup.select_one("form#login-form")
    if form is None:
        raise common.StructureChangedError("No login form found on AusTender's login page.")

    payload = {
        field.get("name"): field.get("value", "")
        for field in form.select("input[type='hidden']")
        if field.get("name")
    }
    payload["Email"] = username
    payload["Password"] = password

    post_url = urljoin(BASE_URL, form.get("action") or LOGIN_URL)
    result = client.post(
        post_url,
        data=payload,
        headers=dict(HEADERS, Referer=str(response.url)),
        follow_redirects=True,
        timeout=25.0,
    )
    return result.status_code < 400


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_listing(html: str, base_url: str = BASE_URL) -> list[str]:
    """Return every ATM detail-page URL on one listing page."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.select(DETAIL_LINK_SELECTOR):
        href = anchor.get("href")
        if href:
            url = urljoin(base_url, href)
            if url not in links:
                links.append(url)
    return links


def parse_detail(html: str) -> tuple[dict, int]:
    """
    Pull an ATM's own fields off its detail page.

    Returns (fields, status_code). If the expected field container isn't
    found on an otherwise real page, returns ({}, SITE_STRUCTURE_CHANGE)
    rather than guessing at a different layout.
    """
    soup = BeautifulSoup(html, "html.parser")

    blocks = soup.select(FIELD_SELECTOR)
    if not blocks:
        return {}, common.SITE_STRUCTURE_CHANGE

    title_el = soup.select_one("p.lead")
    fields = {"title": title_el.get_text(" ", strip=True) if title_el else None}

    for block in blocks:
        label_el = block.select_one("span")
        value_el = block.select_one(".list-desc-inner")
        if label_el is None or value_el is None:
            continue
        label = label_el.get_text(strip=True).rstrip(":")
        fields[label] = value_el.get_text(" ", strip=True)

    # Normalise the fields the rest of the pipeline keys on.
    fields["atm_id"] = fields.pop("ATM ID", None)
    fields["agency"] = fields.pop("Agency", None)
    fields["close_date"] = fields.pop("Close Date & Time", None)
    fields["publish_date"] = fields.pop("Publish Date", None)
    fields["description"] = fields.pop("Description", None)

    if not fields["atm_id"]:
        return {}, common.SITE_STRUCTURE_CHANGE

    return fields, common.SITE_SUCCESS


def find_documents_url(html: str, base_url: str = BASE_URL) -> str | None:
    """
    The detail page's "ATM Documents" link, or None when the ATM has no
    documents page at all (a real, valid state -- not every ATM does).
    """
    anchor = BeautifulSoup(html, "html.parser").select_one(DOCUMENTS_PAGE_SELECTOR)
    if anchor is None or not anchor.get("href"):
        return None
    return urljoin(base_url, anchor["href"])


def format_detail_text(fields: dict, url: str) -> str:
    """Render the parsed fields as the tender's page-text file."""
    lines = [
        "=" * 80,
        f"AUSTENDER DETAILS: {fields.get('title') or fields.get('atm_id')}",
        "=" * 80,
        "",
        f"Detail URL: {url}",
        "",
    ]
    for key, value in fields.items():
        if value:
            lines.append(f"{key}: {value}")
    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


def parse_documents(html: str, base_url: str = BASE_URL) -> list[dict]:
    """
    Every attachment on an /Atm/ViewDocuments page, as {file_name, url}.

    The filename comes from the anchor's title attribute where present, else
    the fileName= query parameter, else a generic placeholder -- the page uses
    all three shapes.
    """
    soup = BeautifulSoup(html, "html.parser")
    documents = []
    for anchor in soup.select(DOCUMENT_LINK_SELECTOR):
        href = anchor.get("href")
        if not href:
            continue
        name = anchor.get("title")
        if not name:
            query = parse_qs(urlparse(href).query)
            name = unquote(query["fileName"][0]) if query.get("fileName") else ""
        documents.append({
            "file_name": common.sanitise_filename(name or "document.bin"),
            "url": urljoin(base_url, href),
        })
    return documents


# ---------------------------------------------------------------------------
# Downloading + extraction
# ---------------------------------------------------------------------------

def process_documents(client, documents: list[dict], folder: str) -> tuple[int, list[dict]]:
    """
    Download every document and extract its text where we can.

    Returns (status_code, attachments). TENDER_PARTIAL means something that
    should have worked didn't: a download failed, or extraction failed on a
    format we support.
    """
    any_failed = False
    attachments = []
    for document in documents:
        try:
            attachment, extracted = common.download_attachment(
                client, document["url"], folder, document["file_name"], headers=HEADERS
            )
        except Exception as e:
            print(f"DOWNLOAD FAILED: {document['url']}: {e}")
            any_failed = True
            continue
        attachments.append(attachment)
        any_failed = any_failed or not extracted
        time.sleep(PAUSE_SECONDS)

    code = common.TENDER_PARTIAL if any_failed else common.SITE_SUCCESS
    return code, attachments


# ---------------------------------------------------------------------------
# Per-ATM and full-run orchestration
# ---------------------------------------------------------------------------

def scrape_opportunity(client, url: str, output_dir: str = "tenders_data") -> tuple[int, dict]:
    """
    Scrape one ATM into its own folder.

    Returns (status_code, tender). `tender` is empty if the page could not
    be parsed. A documents page that redirects to the login form gives
    TENDER_PARTIAL with `documents_gated` set on the tender, so run_scraper
    can tell "not logged in" apart from other partial results.
    """
    response = client.get(url, headers=HEADERS, timeout=30.0)
    response.raise_for_status()
    fields, code = parse_detail(response.text)
    if code != common.SITE_SUCCESS:
        return code, {}

    atm_id = fields["atm_id"]
    folder = common.tender_dir(atm_id, output_dir)
    common.save_page_text(folder, atm_id, format_detail_text(fields, url))

    tender = {
        "tender_id": atm_id,
        "title": fields.get("title"),
        "folder": folder,
        "attachments": [],
        "source_url": url,
        "documents_gated": False,
    }

    documents_url = find_documents_url(response.text)
    if documents_url is None:
        return common.SITE_SUCCESS, tender

    doc_response = client.get(documents_url, headers=HEADERS, timeout=30.0)
    doc_response.raise_for_status()
    if "/login" in str(doc_response.url).lower() or is_login_page(doc_response.text):
        tender["documents_gated"] = True
        return common.TENDER_PARTIAL, tender

    code, tender["attachments"] = process_documents(
        client, parse_documents(doc_response.text), folder
    )
    return code, tender


def collect_all_listing_urls(client, limit: int = 0) -> list[str]:
    """
    Walk every page of the open-ATM list, stopping when a page comes back
    with no new links (end of results) or once `limit` is reached. `limit`
    of 0 means walk every page.

    Raises StructureChangedError if the very first page has no ATM links:
    AusTender always has open ATMs, so an empty first page means the
    listing's markup changed, not that there is nothing to scrape.
    """
    urls, page = [], 1
    while True:
        response = client.get(LIST_URL, params={"page": page}, headers=HEADERS, timeout=30.0)
        response.raise_for_status()
        page_urls = [u for u in parse_listing(response.text) if u not in urls]
        if not page_urls:
            if page == 1:
                raise common.StructureChangedError("No ATM links on the first listing page.")
            break
        urls.extend(page_urls)
        if limit and len(urls) >= limit:
            return urls[:limit]
        page += 1
        time.sleep(PAUSE_SECONDS)
    return urls


def run_scraper(limit: int = 0, output_dir: str = "tenders_data") -> tuple[int, list[dict]]:
    """
    Scrape every open ATM across every page of the listing. `limit` of 0
    means every ATM found.

    Returns (site_code, tenders) -- one entry per ATM scraped, each listing
    the attachment files saved for it. Documents gated behind the login are
    SITE_LOGIN_FAILED when credentials were configured (the login did not
    take) and TENDER_PARTIAL when they weren't (nothing to log in with).
    """
    username, password = credentials()
    have_credentials = bool(username and password)
    tenders: list[dict] = []
    try:
        with httpx.Client(follow_redirects=True) as client:
            if have_credentials:
                if not login(client):
                    return common.SITE_LOGIN_FAILED, tenders
            else:
                print("AUSTENDER_USERNAME/AUSTENDER_PASSWORD not set -- "
                      "attachments will be skipped")

            urls = collect_all_listing_urls(client, limit)

            tender_codes = set()
            for url in urls:
                try:
                    code, tender = scrape_opportunity(client, url, output_dir)
                    if tender:
                        tenders.append(tender)
                        if tender["documents_gated"] and have_credentials:
                            code = common.SITE_LOGIN_FAILED
                    tender_codes.add(code)
                except Exception as e:
                    print(f"AUSTENDER TENDER FAILED: {url}: {e}")
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

    parser = argparse.ArgumentParser(description="Scrape open ATMs from AusTender.")
    parser.add_argument("--limit", type=int, default=5, help="max ATMs (0 = all)")
    parser.add_argument("--output-dir", default="tenders_data")
    args = parser.parse_args()

    code, tenders = run_scraper(limit=args.limit, output_dir=args.output_dir)
    print(f"AusTender run finished with code {code}, {len(tenders)} tenders scraped")


if __name__ == "__main__":
    main()
