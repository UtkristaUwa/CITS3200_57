"""
error_scrapers/tenders_act/scraper.py

Scraper for Tenders ACT. Written test-first, same discipline as
error_scrapers/grant_connect/scraper.py and
error_scrapers/buy_nsw/scraper.py -- every function here exists to
satisfy a specific test in test_cases/test_act.py.

Unlike buy.nsw, ACT needs a login step (like GrantConnect). Unlike
GrantConnect, the document flow is two stages: a tender's detail page
links to a "download docs" page listing every document as a checked
checkbox; that page's form must be POSTed back (with the selected ids)
to actually receive the zip.
"""

import io
import os
import re
import zipfile

import httpx
from bs4 import BeautifulSoup

from error_scrapers import common

BASE_URL = "https://www.tenders.act.gov.au"
LOGIN_URL = f"{BASE_URL}/login"
LIST_URL = f"{BASE_URL}/tenders/open"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}

USERNAME = os.environ.get("ACT_USERNAME")
PASSWORD = os.environ.get("ACT_PASSWORD")

DETAIL_LINK_SELECTOR = "a.tenderRowTitle"

# The container each Overview field (Type, Status, Number, ...) lives in
# on a detail page. If this selector finds nothing on an otherwise
# real-looking page, the site's structure has changed -- this is the
# exact id the structure-changed fixture renames to simulate that.
FIELD_SECTION_SELECTOR = "#opportunityGeneral"

LOGIN_ERROR_TEXT = "Invalid username/password combination"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def parse_login_form(html: str) -> dict:
    """Pull the hidden fields off the supplier login form. Returns an
    empty dict if the form isn't present (e.g. a bot-block page)."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", {"id": "supplierLoginForm"})
    if form is None:
        return {}
    fields = {}
    for hidden in form.find_all("input", {"type": "hidden"}):
        name = hidden.get("name")
        if name:
            fields[name] = hidden.get("value", "")
    return fields


def login_failed(html: str) -> bool:
    """True if the page shows the "wrong username/password" error."""
    return LOGIN_ERROR_TEXT in html


def login(client) -> bool:
    """Log in as a supplier. Returns True on success, False on failure."""
    response = client.get(LOGIN_URL, headers=HEADERS, timeout=30.0)
    fields = parse_login_form(response.text)
    if not fields:
        return False

    data = {
        "username": USERNAME,
        "password": PASSWORD,
        "businessType": fields.get("businessType", "SUPPLIER"),
        "tenantCode": fields.get("tenantCode", "act"),
        "_csrf": fields.get("_csrf", ""),
    }
    response = client.post(LOGIN_URL, data=data, headers=HEADERS, timeout=30.0)
    return not login_failed(response.text)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def parse_listing(html: str, base_url: str = BASE_URL) -> list[str]:
    """Return every tender detail-page URL on one listing page."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.select(DETAIL_LINK_SELECTOR):
        href = anchor.get("href")
        if not href:
            continue
        url = href if href.startswith("http") else f"{base_url}{href}"
        if url not in links:
            links.append(url)
    return links


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def parse_detail(html: str) -> tuple[dict, int]:
    """
    Pull a tender's own fields off its detail page.

    Returns (fields, status_code). If the expected field section isn't
    found on an otherwise real page, returns ({}, SITE_STRUCTURE_CHANGE)
    rather than guessing at a different layout.
    """
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("#tenderTitle")
    title = title_el.get_text(strip=True) if title_el else None

    agency = None
    agency_el = soup.select_one(".weight-bold")
    if agency_el:
        text = agency_el.get_text(" ", strip=True)
        agency = text.replace("Directorate/Agency", "").strip()

    section = soup.select_one(FIELD_SECTION_SELECTOR)
    if section is None:
        return {}, common.SITE_STRUCTURE_CHANGE

    fields = {"title": title, "agency": agency}
    for row in section.select(".row"):
        cells = row.find_all("div", recursive=False)
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True).rstrip(":")
        value = cells[1].get_text(" ", strip=True)
        fields[label] = value

    fields["tender_type"] = fields.pop("Type", None)
    fields["tender_status"] = fields.pop("Status", None)
    fields["tender_code"] = fields.pop("Number", None)

    description_el = soup.select_one("#tenderDescription")
    if description_el:
        fields["description"] = description_el.get_text("\n", strip=True).replace(
            "Description", "", 1
        ).strip()

    closing_el = soup.select_one("#tenderClosingTime")
    if closing_el:
        fields["closing_date"] = closing_el.get_text(" ", strip=True).replace(
            "Tender closes at", ""
        ).strip()

    return fields, common.SITE_SUCCESS


def find_download_docs_url(html: str, base_url: str = BASE_URL) -> str | None:
    """
    Locate the "Download Now" link on a detail page. Returns None if
    there is nothing to download.
    """
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href*='downloadSpecDocs']"):
        if "download now" in anchor.get_text(" ", strip=True).lower():
            href = anchor["href"]
            return href if href.startswith("http") else f"{base_url}{href}"
    return None

def _format_detail_text(fields: dict) -> str:
    """Render a tender's own detail-page fields as its .txt file, the
    same role GrantConnect's save_page_text() and buy.nsw's summary-PDF
    text play for their sources."""
    lines = [
        "=" * 80,
        f"TENDERS ACT: {fields.get('title') or fields.get('tender_code')}",
        "=" * 80,
        "",
    ]
    for key, value in fields.items():
        if value:
            lines.append(f"{key}: {value}")
    lines += ["", "=" * 80, ""]
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Download-docs page parsing
# ---------------------------------------------------------------------------

def parse_download_form(html: str) -> dict:
    """
    Pull the download form's action, hidden fields, and every checked
    document id off the "download docs" page.

    Always returns a dict with a "code" key. If the checkbox field name
    the form is expected to use isn't found on an otherwise real page,
    code is SITE_STRUCTURE_CHANGE and "ids" is empty.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", {"id": "spec"})

    checkboxes = form.select("input[name='ids[]']") if form else []
    if not checkboxes:
        return {"code": common.SITE_STRUCTURE_CHANGE, "ids": []}

    fields = {"code": common.SITE_SUCCESS, "action": form.get("action")}
    for hidden in form.find_all("input", {"type": "hidden"}):
        name = hidden.get("name")
        if name:
            fields[name] = hidden.get("value", "")

    fields["ids"] = [
        cb["value"] for cb in checkboxes
        if cb.get("checked") is not None and cb.get("value")
    ]
    return fields


# ---------------------------------------------------------------------------
# Per-opportunity and full-run orchestration
# ---------------------------------------------------------------------------

def scrape_opportunity(client, url: str, output_dir: str = "tenders_data") -> tuple[int, dict]:
    """Scrape one tender into its own folder. Returns (status_code, tender)."""
    response = client.get(url, headers=HEADERS, timeout=30.0)
    fields, code = parse_detail(response.text)
    if code != common.SITE_SUCCESS:
        return code, {}

    tender_code = fields.get("tender_code") or "UNKNOWN"
    folder = common.tender_dir(tender_code, output_dir)
    common.save_page_text(folder, tender_code, _format_detail_text(fields))
    download_url = find_download_docs_url(response.text)

    attachments = []
    if download_url:
        try:
            docs_response = client.get(download_url, headers=HEADERS, timeout=30.0)
            form = parse_download_form(docs_response.text)
            if form["code"] != common.SITE_SUCCESS or not form["ids"]:
                tender = {"title": fields.get("title"), "folder": folder,
                          "attachments": [], **fields}
                return common.TENDER_PARTIAL, tender

            post_data = {"opportunityId": form.get("opportunityId", ""),
                         "_csrf": form.get("_csrf", "")}
            post_url = (form["action"] if form["action"].startswith("http")
                        else f"{BASE_URL}{form['action']}")
            post_data = [
                ("opportunityId", form.get("opportunityId", "")),
                ("_csrf", form.get("_csrf", "")),
            ]
            post_data += [("ids[]", doc_id) for doc_id in form["ids"]]
            zip_response = client.post(post_url, data=post_data,
                                        headers=HEADERS, timeout=60.0)

            extractors = {
                ".pdf": common.extract_pdf,
                ".docx": common.extract_docx,
                ".xlsx": common.extract_xlsx,
            }
            any_failed = False
            with zipfile.ZipFile(io.BytesIO(zip_response.content)) as zf:
                for name in zf.namelist():
                    file_name = os.path.basename(name)
                    if not file_name:
                        continue
                    content = zf.read(name)
                    raw_path = common.save_attachment(folder, file_name, content)
                    extension = os.path.splitext(file_name)[1].lower()
                    extractor = extractors.get(extension)
                    if extractor is not None:
                        try:
                            text = extractor(raw_path)
                            common.save_extracted_text(folder, file_name, text)
                        except common.ExtractionError:
                            any_failed = True
                    attachments.append({"file_name": file_name, "content_type": None})

            if any_failed:
                tender = {"title": fields.get("title"), "folder": folder,
                          "attachments": attachments, **fields}
                return common.TENDER_PARTIAL, tender
        except Exception:
            return common.TENDER_PARTIAL, {
                "title": fields.get("title"), "folder": folder, "attachments": [],
            }

    tender = {"title": fields.get("title"), "folder": folder,
              "attachments": attachments, **fields}
    return common.SITE_SUCCESS, tender


def collect_all_listing_urls(client, limit: int = 0) -> list[str]:
    """Walk the open-tenders listing. `limit` of 0 means every tender found."""
    response = client.get(LIST_URL, headers=HEADERS, timeout=30.0)
    urls = parse_listing(response.text)
    if limit:
        return urls[:limit]
    return urls


def run_scraper(limit: int = 0, output_dir: str = "tenders_data",
                 client_override=None) -> tuple[int, list[dict]]:
    """
    Log in, then scrape every current open tender. `limit` of 0 means
    every tender found.

    Returns (site_code, tenders) -- one entry per tender successfully
    scraped, each with the attachment files it saved.
    """
    tenders: list[dict] = []
    try:
        if client_override is not None:
            client = client_override
            if not login(client):
                return common.SITE_LOGIN_FAILED, tenders
            urls = collect_all_listing_urls(client, limit)
            return _scrape_all(client, urls, output_dir, tenders)

        with httpx.Client(follow_redirects=True) as client:
            if not login(client):
                return common.SITE_LOGIN_FAILED, tenders
            urls = collect_all_listing_urls(client, limit)
            return _scrape_all(client, urls, output_dir, tenders)

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            return common.SITE_RATE_LIMITED, tenders
        return common.SITE_TOTAL_FAILURE, tenders
    except (httpx.ConnectError, ConnectionError):
        return common.SITE_TOTAL_FAILURE, tenders

def run_scraper_via_browser(limit: int = 0, output_dir: str = "tenders_data") -> tuple[int, list[dict]]:
    """
    Same as run_scraper(), but drives everything through a real
    browser -- required because tenders.act.gov.au blocks plain httpx
    requests with a 403 even on the bare login page, confirmed live.
    """
    from error_scrapers.tenders_act.browser import BrowserSession

    tenders: list[dict] = []
    try:
        with BrowserSession(download_dir=output_dir) as session:
            if not session.login():
                return common.SITE_LOGIN_FAILED, tenders

            listing_html = session.get(LIST_URL)
            urls = parse_listing(listing_html)
            if limit:
                urls = urls[:limit]

            tender_codes = set()
            for url in urls:
                try:
                    detail_html = session.get(url)
                    fields, code = parse_detail(detail_html)
                    if code != common.SITE_SUCCESS:
                        tender_codes.add(code)
                        continue

                    tender_code = fields.get("tender_code") or "UNKNOWN"
                    folder = common.tender_dir(tender_code, output_dir)
                    common.save_page_text(folder, tender_code, _format_detail_text(fields))
                    download_url = find_download_docs_url(detail_html)

                    attachments = []
                    if download_url:
                        docs_html = session.get(download_url)
                        form = parse_download_form(docs_html)
                        if form["code"] == common.SITE_SUCCESS and form["ids"]:
                            zip_path = session.download_via_form(download_url, form["ids"])
                            attachments = _extract_zip_into_folder(zip_path, folder)
                            os.remove(zip_path)

                    tenders.append({"title": fields.get("title"), "folder": folder,
                                     "attachments": attachments, **fields})
                except Exception:
                    tender_codes.add(common.TENDER_PARTIAL)
                    continue

            if not tender_codes:
                return common.SITE_SUCCESS, tenders
            if common.SITE_STRUCTURE_CHANGE in tender_codes:
                return common.SITE_STRUCTURE_CHANGE, tenders
            return common.TENDER_PARTIAL, tenders

    except Exception:
        import traceback
        traceback.print_exc()
        return common.SITE_TOTAL_FAILURE, tenders


def _extract_zip_into_folder(zip_path: str, folder: str) -> list[dict]:
    """Unzip a downloaded document package into folder, extracting text
    from each supported file type. Returns the attachment manifest."""
    import zipfile

    extractors = {
        ".pdf": common.extract_pdf,
        ".docx": common.extract_docx,
        ".xlsx": common.extract_xlsx,
    }
    attachments = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            file_name = os.path.basename(name)
            if not file_name:
                continue
            content = zf.read(name)
            raw_path = common.save_attachment(folder, file_name, content)
            extension = os.path.splitext(file_name)[1].lower()
            extractor = extractors.get(extension)
            if extractor is not None:
                try:
                    text = extractor(raw_path)
                    common.save_extracted_text(folder, file_name, text)
                except common.ExtractionError:
                    pass
            attachments.append({"file_name": file_name, "content_type": None})
    return attachments


def _scrape_all(client, urls, output_dir, tenders):
    tender_codes = set()
    for url in urls:
        try:
            code, tender = scrape_opportunity(client, url, output_dir)
            if tender:
                tenders.append(tender)
            if code != common.SITE_SUCCESS:
                tender_codes.add(code)
        except Exception:
            tender_codes.add(common.TENDER_PARTIAL)
            continue

    if not tender_codes:
        return common.SITE_SUCCESS, tenders
    if common.SITE_STRUCTURE_CHANGE in tender_codes:
        return common.SITE_STRUCTURE_CHANGE, tenders
    return common.TENDER_PARTIAL, tenders


def main():
    import logging
    logging.basicConfig(level=logging.INFO)
    code, tenders = run_scraper_via_browser(limit=2)
    print(f"Tenders ACT run finished with code {code}, {len(tenders)} tenders scraped")


if __name__ == "__main__":
    main()