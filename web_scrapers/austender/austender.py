"""
Web scraper for AusTender (https://www.tenders.gov.au) -- federal ATMs.

Output: one directory per tender under tenders_data/, containing the scraped
detail text as <ATM ID>.txt, a documents.json manifest, and every attachment
that could be downloaded.

Login:
    Attachment downloads are behind a registered-user session -- an anonymous
    request to /Atm/ViewDocuments/<id> is redirected to the login form. Set

        AUSTENDER_USERNAME / AUSTENDER_PASSWORD

    in the environment (Secret Manager in Cloud Run). Without them the scraper
    still runs and still writes one folder per tender; the manifest simply
    records that the attachments need a login.

Usage:
    python -m web_scrapers.austender.austender --limit 10
"""

import argparse
import logging
import os
import re
import time
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from web_scrapers.common import (
    Document,
    TenderRecord,
    download_document,
    sanitise_filename,
    tender_dir,
    write_manifest,
    write_tender_text,
)

log = logging.getLogger(__name__)

SOURCE_ID = "austender"
BASE_URL = "https://www.tenders.gov.au"
LOGIN_URL = f"{BASE_URL}/RegisteredUser/Login"
ATM_LIST_URL = f"{BASE_URL}/atm"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Both attachment routes on the documents page. Selected on the route rather
# than on link text, which varies ("Download", the filename, an icon only).
DOCUMENT_LINK_SELECTOR = (
    "a[href*='/Atm/DownloadSoftCopy/'], a[href*='/Atm/DownloadAddenda/']"
)


def credentials():
    """Return (username, password) from the environment, or (None, None)."""
    return (
        os.environ.get("AUSTENDER_USERNAME") or None,
        os.environ.get("AUSTENDER_PASSWORD") or None,
    )


def log_in(client, username, password):
    """
    Establish a registered-user session on `client`.

    AusTender's login form carries an anti-forgery token and names its fields
    differently across releases, so the form is read back and every hidden
    input is replayed rather than posting a fixed payload.

    Returns True if the response looks like a signed-in session.
    """
    response = client.get(LOGIN_URL, headers=HEADERS, timeout=20.0)
    response.raise_for_status()

    form = BeautifulSoup(response.text, "html.parser").select_one(
        "form[action*='Login'], form"
    )
    if form is None:
        log.error("no login form found at %s", LOGIN_URL)
        return False

    payload = {
        field.get("name"): field.get("value", "")
        for field in form.select("input[type='hidden']")
        if field.get("name")
    }
    email_input = form.select_one(
        "input[type='text'], input[type='email'], input[name*='Email'], input[name*='User']"
    )
    password_input = form.select_one("input[type='password'], input[name*='Pass']")
    payload[email_input.get("name") if email_input else "Email"] = username
    payload[password_input.get("name") if password_input else "Password"] = password

    post_url = urljoin(BASE_URL, form.get("action") or LOGIN_URL)
    headers = dict(HEADERS, Referer=str(response.url))
    result = client.post(
        post_url, data=payload, headers=headers, follow_redirects=True, timeout=25.0
    )

    body = result.text.lower()
    if "invalid" in body or "incorrect" in body:
        log.error("AusTender rejected the credentials")
        return False
    if "log off" in body or "logout" in body:
        log.info("AusTender session established")
        return True
    # Signed in but the marker moved: treat the cookie jar as authoritative and
    # let the first document request prove it either way.
    log.warning("could not confirm AusTender login; continuing with session cookies")
    return True


def parse_listing(html):
    """Return the detail-page URLs on one /atm listing page, in order, deduped."""
    soup = BeautifulSoup(html, "html.parser")
    urls, seen = [], set()
    for anchor in soup.select("a[href*='/Atm/Show/']"):
        href = anchor.get("href")
        if href and href not in seen:
            seen.add(href)
            urls.append(urljoin(BASE_URL, href))
    return urls


def parse_detail(html, detail_url):
    """
    Pull the structured fields off an ATM detail page.

    Returns {'title', 'atm_id', 'metadata', 'documents_url'}. `atm_id` falls
    back to the URL's GUID so a tender with an unreadable ID still gets its
    own folder rather than colliding with another.
    """
    soup = BeautifulSoup(html, "html.parser")

    title_element = soup.select_one("p.lead, h1")
    title = title_element.get_text(strip=True) if title_element else "Untitled Tender"

    metadata = {}
    for block in soup.select("div.list-desc"):
        label = block.select_one("span > label, label, span")
        value = block.select_one("div.list-desc-inner")
        if label and value:
            metadata[label.get_text(strip=True).rstrip(":")] = value.get_text(
                separator=" ", strip=True
            )

    atm_id = metadata.get("ATM ID") or detail_url.rstrip("/").split("/")[-1]
    metadata.setdefault("ATM ID", atm_id)

    documents_link = soup.select_one("a[href*='/Atm/ViewDocuments/']")
    documents_url = (
        urljoin(BASE_URL, documents_link.get("href")) if documents_link else None
    )

    return {
        "title": title,
        "atm_id": atm_id,
        "metadata": metadata,
        "documents_url": documents_url,
    }


def format_detail(title, detail_url, metadata):
    """Render the scraped fields as the tender's .txt file."""
    lines = [
        "=" * 80,
        f"AUSTENDER DETAILS: {title}",
        "=" * 80,
        "",
        f"Detail URL: {detail_url}",
        f"Scraped At: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "STRUCTURED ATTRIBUTES:",
        "-" * 40,
    ]
    lines += [f"{key}: {value}" for key, value in metadata.items()]
    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


def parse_documents(html, base_url=None):
    """
    Return the Documents advertised on an /Atm/ViewDocuments page.

    Filenames come from the anchor's title attribute where present, else the
    fileName= query parameter, else a generic placeholder -- the page uses all
    three shapes.
    """
    base_url = base_url or BASE_URL
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
        documents.append(
            Document(
                file_name=sanitise_filename(name or "document.bin"),
                url=urljoin(base_url, href),
            )
        )
    return documents


def looks_like_login_page(html):
    """True if AusTender served the login form instead of the requested page."""
    return BeautifulSoup(html, "html.parser").select_one("input[type='password']") is not None


def collect_documents(client, documents_url):
    """
    Fetch the documents page and return (documents, requires_login).

    An anonymous request lands on the login form; that is reported as
    requires_login rather than as an error, because it is the expected outcome
    when no credentials are configured.
    """
    if not documents_url:
        return [], False

    response = client.get(documents_url, headers=HEADERS, timeout=20.0)
    if response.status_code != 200:
        log.warning("documents page %s returned HTTP %s", documents_url, response.status_code)
        return [], False
    if "login" in str(response.url).lower() or looks_like_login_page(response.text):
        log.info("documents for %s need a login", documents_url)
        return [], True
    return parse_documents(response.text), False


def scrape_tender(client, detail_url, output_dir=None, pause=0.3):
    """
    Scrape one tender into its own directory.

    Returns the TenderRecord that was written to documents.json.
    """
    response = client.get(detail_url, headers=HEADERS, timeout=20.0)
    response.raise_for_status()
    detail = parse_detail(response.text, detail_url)

    folder = tender_dir(detail["atm_id"], output_dir)
    write_tender_text(
        folder, format_detail(detail["title"], detail_url, detail["metadata"])
    )

    documents, requires_login = collect_documents(client, detail["documents_url"])
    for document in documents:
        download_document(client, document, folder, headers=HEADERS)
        if document.error:
            log.warning("  %s: %s", document.file_name, document.error)
        time.sleep(pause)

    record = TenderRecord(
        reference=detail["atm_id"],
        source_id=SOURCE_ID,
        source_url=detail_url,
        documents=documents,
        documents_require_login=requires_login,
    )
    write_manifest(folder, record)
    log.info(
        "%s -> %s (%d/%d document(s))",
        detail["atm_id"],
        folder.name,
        record.documents_downloaded,
        record.documents_advertised,
    )
    return record


def run_scraper(limit=10, output_dir=None, pause=0.5):
    """
    Scrape the first `limit` open ATMs into one directory each.

    Returns the list of TenderRecords. Individual tenders that fail are logged
    and skipped so one bad page cannot end the run.
    """
    username, password = credentials()
    records = []

    with httpx.Client(follow_redirects=True) as client:
        if username and password:
            if not log_in(client, username, password):
                log.warning("continuing without a session -- documents will be skipped")
        else:
            log.warning(
                "AUSTENDER_USERNAME/AUSTENDER_PASSWORD not set -- "
                "tender text will be scraped but attachments need a login"
            )

        page, scraped = 1, 0
        while scraped < limit:
            listing = client.get(
                f"{ATM_LIST_URL}?page={page}", headers=HEADERS, timeout=20.0
            )
            if listing.status_code != 200:
                log.error("listing page %d returned HTTP %s", page, listing.status_code)
                break

            urls = parse_listing(listing.text)
            if not urls:
                log.info("no tenders on page %d -- end of list", page)
                break

            for detail_url in urls:
                if scraped >= limit:
                    break
                scraped += 1
                try:
                    records.append(scrape_tender(client, detail_url, output_dir))
                except Exception as exc:
                    log.error("[%d/%d] %s failed: %s", scraped, limit, detail_url, exc)
                time.sleep(pause)

            page += 1

    log.info("AusTender: scraped %d tender(s)", len(records))
    return records


def main():
    parser = argparse.ArgumentParser(description="Scrape open ATMs from AusTender.")
    parser.add_argument("--limit", type=int, default=10, help="max tenders to scrape")
    parser.add_argument("--output-dir", default=None, help="defaults to tenders_data/")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    run_scraper(limit=args.limit, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
