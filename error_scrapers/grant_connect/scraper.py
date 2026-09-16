"""
error_scrapers/grant_connect/scraper.py
 
Scraper for GrantConnect (grants.gov.au). Written test-first: every
function here exists to satisfy a specific test in
test_cases/test_grantconnect.py.
 
Per opportunity, saves three things into its own folder (via common.py's
tender_dir/save_page_text/save_attachment/save_extracted_text):
    <GO_ID>.txt          -- text from the detail page's own fields
    <attachment files>   -- raw downloads, as-is
    <attachment>.txt     -- extracted text per attachment
 
Returns a per-opportunity status code from scrape_opportunity(), and one
site-level code from run_scraper() for the whole run.
"""
 
import os

import re
 
import httpx
from bs4 import BeautifulSoup
 
from error_scrapers import common

from dotenv import load_dotenv
load_dotenv()
 
SOURCE_ID = "grantconnect"
BASE_URL = "https://www.grants.gov.au"
LOGIN_URL = f"{BASE_URL}/RegisteredUser/Login"
LIST_URL = f"{BASE_URL}/Go/List"
 
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
 
# The exact container the detail page's fields live in. If this selector
# finds nothing on an otherwise normal-looking page, the site's structure
# has changed -- this is the same class the structure-changed fixture
# deliberately renames to "list-fields" to simulate that.
FIELD_SELECTOR = "div.list-desc"
 
# Detail page links, from the public listing.
DETAIL_LINK_SELECTOR = "a[href*='/Go/Show?GoUuid=']"
 
# Attachment download links, from the (logged-in) documents page.
DOCUMENT_LINK_SELECTOR = "a[href*='/Go/DownloadDocument']"
 
 
def credentials():
    """Return (username, password) from the environment, or (None, None)."""
    return (
        os.environ.get("GRANTCONNECT_USERNAME") or None,
        os.environ.get("GRANTCONNECT_PASSWORD") or None,
    )
 
 
# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
 
def login_succeeded(html: str) -> bool:
    """
    True if `html` is what GrantConnect shows after a SUCCESSFUL login.
 
    A successful login shows a "Welcome, <name>" panel. A failed login
    stays on the login page and shows a red "Error Message" block instead
    (wrong password, or an account suspended from inactivity -- both look
    the same from here, and both mean "cannot proceed").
    """
    return "Welcome," in html
 
 
def login_failed(html: str) -> bool:
    """True if `html` is GrantConnect's failed-login error page."""
    return "Error Message" in html
 
 
def login(client: httpx.Client) -> bool:
    username, password = credentials()

    # First GET the login page -- needed for its anti-forgery token,
    # which the server rejects the POST without.
    get_response = client.get(LOGIN_URL, headers=HEADERS, timeout=20.0)
    form = BeautifulSoup(get_response.text, "html.parser").select_one("form")
    if form is None:
        raise common.StructureChangedError(
            "No login form found on GrantConnect's login page."
        )

    payload = {
        field.get("name"): field.get("value", "")
        for field in form.select("input[type='hidden']")
        if field.get("name")
    }
    payload["Email"] = username
    payload["Password"] = password

    response = common.submit_login(client, LOGIN_URL, payload)

    if login_succeeded(response.text):
        return True
    if login_failed(response.text):
        return False

    raise common.StructureChangedError(
        "GrantConnect login response matched neither the known success "
        "nor known failure pattern -- the login page may have changed."
    )
 
 
# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
 
def parse_detail(html: str) -> tuple[dict, int]:
    """
    Pull the tender's own fields off a Go/Show detail page.
 
    Returns (fields, status_code). If the expected field container isn't
    found on an otherwise real page, returns ({}, SITE_STRUCTURE_CHANGE)
    rather than guessing at a different layout.
    """
    soup = BeautifulSoup(html, "html.parser")
 
    title_el = soup.select_one("p.font20")
    title = title_el.get_text(strip=True) if title_el else None
 
    blocks = soup.select(FIELD_SELECTOR)
    if not blocks:
        return {}, common.SITE_STRUCTURE_CHANGE
 
    fields = {"title": title}
    for block in blocks:
        label_el = block.select_one("span")
        value_el = block.select_one(".list-desc-inner")
        if label_el is None or value_el is None:
            continue
        label = label_el.get_text(strip=True).rstrip(":")
        value = value_el.get_text(" ", strip=True)
        fields[label] = value
 
    # "GO ID" is the field GrantConnect prints; normalise it to the key
    # the rest of the pipeline (and the tests) expect.
    fields["go_id"] = fields.pop("GO ID", None)
    fields["agency"] = fields.pop("Agency", None)
    fields["description"] = fields.pop("Description", None)
 
    return fields, common.SITE_SUCCESS
 
 
def format_detail_text(fields: dict) -> str:
    """Render the parsed fields as the opportunity's .txt file."""
    lines = [
        "=" * 80,
        f"GRANTCONNECT DETAILS: {fields.get('title') or fields.get('go_id')}",
        "=" * 80,
        "",
    ]
    for key, value in fields.items():
        if value:
            lines.append(f"{key}: {value}")
    lines += ["", "=" * 80, ""]
    return "\n".join(lines)
 
 
def parse_listing(html: str, base_url: str = BASE_URL) -> list[str]:
    """Return every opportunity detail-page URL on one listing page."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.select(DETAIL_LINK_SELECTOR):
        href = anchor.get("href")
        if href:
            url = href if href.startswith("http") else f"{base_url}{href}"
            if url not in links:
                links.append(url)
    return links
 
 
def parse_documents(html: str, base_url: str = BASE_URL) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    documents = []
    for anchor in soup.select(DOCUMENT_LINK_SELECTOR):
        href = anchor.get("href")
        if not href:
            continue
        raw_name = anchor.get_text(strip=True)
        # strip a trailing size like "582 KB" or "1.42 MB" glued onto the filename
        name = re.sub(r'\d+(\.\d+)?\s*(KB|MB)\s*$', '', raw_name).strip()
        url = href if href.startswith("http") else f"{base_url}{href}"
        documents.append({
            "file_name": common.sanitise_filename(name or "document.bin"),
            "url": url,
        })
    return documents

# ---------------------------------------------------------------------------
# Downloading + extraction
# ---------------------------------------------------------------------------
 
def process_documents(client, documents: list[dict], output_dir: str) -> int:
    """
    Download every document, extract its text, and save both into
    output_dir. Returns SUCCESS if everything worked, TENDER_PARTIAL if
    any single document failed to download or extract.
    """
    any_failed = False
 
    for document in documents:
        try:
            response = client.get(document["url"], headers=HEADERS, timeout=60.0)
            response.raise_for_status()
        except Exception:
            any_failed = True
            continue
 
        raw_path = common.save_attachment(
            output_dir, document["file_name"], response.content
        )
 
        try:
            extension = os.path.splitext(document["file_name"])[1].lower()
            if extension == ".pdf":
                text = common.extract_pdf(raw_path)
            elif extension == ".docx":
                text = common.extract_docx(raw_path)
            elif extension == ".xlsx":
                text = common.extract_xlsx(raw_path)
            else:
                any_failed = True
                continue
            common.save_extracted_text(output_dir, document["file_name"], text)
        except common.ExtractionError as e:
            print(f"EXTRACTION FAILED: {e}")
            any_failed = True
 
    return common.TENDER_PARTIAL if any_failed else common.SITE_SUCCESS
 
 
# ---------------------------------------------------------------------------
# Per-opportunity and full-run orchestration
# ---------------------------------------------------------------------------
 
def scrape_opportunity(client, url: str, output_dir: str = "tenders_data") -> int:
    """Scrape one opportunity into its own folder. Returns its status code."""
    response = client.get(url, headers=HEADERS, timeout=30.0)
    response.raise_for_status()
    fields, code = parse_detail(response.text)
    if code != common.SITE_SUCCESS:
        return code
 
    go_id = fields.get("go_id") or "UNKNOWN"
    folder = common.tender_dir(go_id, output_dir)
    common.save_page_text(folder, go_id, format_detail_text(fields))
 
    documents_url = url.replace("/Go/Show", "/Go/ViewDocuments")
    doc_response = client.get(documents_url, headers=HEADERS, timeout=30.0)
    doc_response.raise_for_status()
    documents = parse_documents(doc_response.text)
 
    return process_documents(client, documents, folder)
 
 
def collect_all_listing_urls(client, limit: int = 0) -> list[str]:
    """
    Walk every page of the opportunity list, stopping when a page comes
    back with no new links (end of results) or once `limit` is reached.
    `limit` of 0 means walk every page.
    """
    urls, page = [], 1
    while True:
        response = client.get(LIST_URL, params={"page": page}, headers=HEADERS, timeout=30.0)
        response.raise_for_status()
        page_urls = parse_listing(response.text)
        if not page_urls:
            break  # no more results
        urls.extend(page_urls)
        if limit and len(urls) >= limit:
            return urls[:limit]
        page += 1
    return urls


def run_scraper(limit: int = 0) -> int:
    """
    Log in, then scrape every current opportunity across every page of
    the listing. `limit` of 0 means every opportunity found.

    Returns the site-level code for the whole run. A failed login stops
    the run immediately -- no opportunities are attempted.
    """
    try:
        with httpx.Client(follow_redirects=True) as client:
            if not login(client):
                return common.SITE_LOGIN_FAILED

            urls = collect_all_listing_urls(client, limit)
 
            tender_codes = set()
            for url in urls:
                try:
                    code = scrape_opportunity(client, url)
                    if code != common.SITE_SUCCESS:
                        tender_codes.add(code)
                except Exception:
                    tender_codes.add(common.TENDER_PARTIAL)
                    continue

        if not tender_codes:
            return common.SITE_SUCCESS
        if common.SITE_STRUCTURE_CHANGE in tender_codes:
            return common.SITE_STRUCTURE_CHANGE
        return common.TENDER_PARTIAL
 
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            return common.SITE_RATE_LIMITED
        return common.SITE_TOTAL_FAILURE
    except (httpx.ConnectError, ConnectionError):
        return common.SITE_TOTAL_FAILURE
 
 
def main():
    import logging
    logging.basicConfig(level=logging.INFO)
    code = run_scraper(limit=20)
    print(f"GrantConnect run finished with code {code}")
 
 
if __name__ == "__main__":
    main()