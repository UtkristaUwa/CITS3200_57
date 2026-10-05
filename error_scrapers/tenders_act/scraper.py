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

import contextlib
import io
import os
import re
import zipfile
import time

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from error_scrapers import common, reporting

load_dotenv()

log = reporting.site_logger("TENDERS_ACT")

SOURCE_ID = "tenders_act"
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

def credentials():
    """Return (username, password) from the environment, or (None, None).
    Read on every call rather than at import, so a .env loaded after this
    module is imported is still picked up."""
    return (
        os.environ.get("ACT_USERNAME") or None,
        os.environ.get("ACT_PASSWORD") or None,
    )


class BotBlockedError(Exception):
    """Raised when Cloudflare serves its challenge instead of the page."""


def is_blocked(title: str, html: str) -> bool:
    """
    True if what loaded is a Cloudflare challenge or block page, not the
    site. Tenders ACT sits behind Cloudflare, and from Cloud Run's address
    range it sometimes serves the "Just a moment..." interstitial in place
    of the login page.
    """
    title = (title or "").lower()
    if "attention required" in title or "just a moment" in title:
        return True
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True).lower()
    return "you have been blocked" in text or "verify you are human" in text

DETAIL_LINK_SELECTOR = "a.tenderRowTitle"

# The container each Overview field (Type, Status, Number, ...) lives in
# on a detail page. If this selector finds nothing on an otherwise
# real-looking page, the site's structure has changed -- this is the
# exact id the structure-changed fixture renames to simulate that.
FIELD_SECTION_SELECTOR = "#opportunityGeneral"

LOGIN_ERROR_TEXT = "Invalid username/password combination"

# Signed-in pages carry a Log Out link; anonymous ones carry Log In.
SIGNED_IN_SELECTOR = "a[href='/logout']"

# How many times a lapsed session is signed in again before the run gives up.
MAX_RELOGINS = 3


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
    username, password = credentials()
    if not username or not password:
        log.error("login: ACT_USERNAME / ACT_PASSWORD are not set")
        return False
    response = client.get(LOGIN_URL, headers=HEADERS, timeout=30.0)
    fields = parse_login_form(response.text)
    if not fields:
        return False

    data = {
        "username": username,
        "password": password,
        "businessType": fields.get("businessType", "SUPPLIER"),
        "tenantCode": fields.get("tenantCode", "act"),
        "_csrf": fields.get("_csrf", ""),
    }
    response = client.post(LOGIN_URL, data=data, headers=HEADERS, timeout=30.0)
    return not login_failed(response.text)


def is_signed_in(html: str) -> bool:
    """True if the page was served to a signed-in supplier."""
    return BeautifulSoup(html or "", "html.parser").select_one(SIGNED_IN_SELECTOR) is not None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def listing_record_count(html: str) -> int | None:
    """The listing's own "Records: 22" total, or None if the page has none."""
    pager = BeautifulSoup(html or "", "html.parser").select_one("p.paging")
    match = re.search(r"Records:\s*(\d+)", pager.get_text(" ", strip=True)) if pager else None
    return int(match.group(1)) if match else None


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
    common.add_source_url(folder, tender_code, url)
    download_url = find_download_docs_url(response.text)

    attachments = []
    if download_url:
        try:
            docs_response = client.get(download_url, headers=HEADERS, timeout=30.0)
            form = parse_download_form(docs_response.text)
            if form["code"] != common.SITE_SUCCESS or not form["ids"]:
                tender = {"title": fields.get("title"), "folder": folder,
                          "attachments": [], "source_url": url, **fields}
                return common.TENDER_PARTIAL, tender

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
                          "attachments": attachments, "source_url": url, **fields}
                return common.TENDER_PARTIAL, tender
        except Exception:
            return common.TENDER_PARTIAL, {
                "title": fields.get("title"), "folder": folder, "attachments": [],
                "source_url": url, **fields,
            }

    tender = {"title": fields.get("title"), "folder": folder,
              "attachments": attachments, "source_url": url, **fields}
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

def _scrape_documents(session, download_url: str, folder: str) -> tuple[list[dict], int, bool]:
    """
    Download and unpack one tender's document package.

    Returns (attachments, advertised, ok). `advertised` is the number of
    documents the download page offered. ok is False when the page had no
    usable form, or fewer files came out than were offered, or any file's
    text could not be extracted -- the caller reports that as TENDER_PARTIAL.
    """
    docs_html = session.get(download_url)
    form = parse_download_form(docs_html)
    if form["code"] != common.SITE_SUCCESS or not form["ids"]:
        log.warning("       documents: the download page had no usable form (%s)",
                    reporting.code_name(form["code"]))
        return [], 0, False

    zip_path = session.download_via_form(download_url, form["ids"])
    try:
        attachments, any_failed = common.unpack_zip(zip_path, folder)
    finally:
        with contextlib.suppress(OSError):
            os.remove(zip_path)
    advertised = len(form["ids"])
    reporting.documents_line(log, len(attachments), advertised)
    return attachments, advertised, not any_failed and len(attachments) >= advertised


def _run_once(limit: int = 0, output_dir: str = "tenders_data") -> tuple[int, list[dict]]:
    """
    One full browser attempt: sign in, walk the listing, scrape every tender.
    """
    from error_scrapers.tenders_act.browser import BrowserSession

    tenders: list[dict] = []
    try:
        with BrowserSession(download_dir=output_dir) as session:
            if not session.login():
                log.error("login FAILED -- see the login lines just above for why")
                return common.SITE_LOGIN_FAILED, tenders
            log.info("login: OK")

            listing_html = session.get(LIST_URL)
            urls = parse_listing(listing_html)
            advertised_total = listing_record_count(listing_html)
            if advertised_total is not None and len(urls) < advertised_total and not limit:
                log.error("listing: the page says %d record(s) but only %d link(s) were "
                          "found -- some tenders are not being seen", advertised_total, len(urls))
            if limit:
                urls = urls[:limit]
            log.info("listing: %d tender link(s) found", len(urls))

            tender_codes = set()
            all_codes = []
            relogins = 0
            for index, url in enumerate(urls, start=1):
                log.info("(%d/%d) fetching %s", index, len(urls), url)
                try:
                    detail_html = session.get(url)
                    if not is_signed_in(detail_html):
                        # The session lapsed mid-run. Sign in again (capped) and retry.
                        if relogins >= MAX_RELOGINS:
                            log.error("session lapsed %d times -- stopping the run", relogins)
                            tender_codes.add(common.SITE_LOGIN_FAILED)
                            break
                        relogins += 1
                        log.warning("       signed out -- signing in again (%d/%d)",
                                    relogins, MAX_RELOGINS)
                        if not session.login():
                            log.error("login: FAILED on re-login -- stopping the run")
                            tender_codes.add(common.SITE_LOGIN_FAILED)
                            break
                        detail_html = session.get(url)

                    fields, code = parse_detail(detail_html)
                    if code != common.SITE_SUCCESS:
                        tender_codes.add(code)
                        all_codes.append(code)
                        reporting.tender_line(log, index, len(urls), url, code)
                        continue

                    tender_code = fields.get("tender_code") or "UNKNOWN"
                    folder = common.tender_dir(tender_code, output_dir)
                    common.save_page_text(folder, tender_code, _format_detail_text(fields))
                    common.add_source_url(folder, tender_code, url)
                    download_url = find_download_docs_url(detail_html)

                    attachments, tender_code_result = [], common.SITE_SUCCESS
                    if download_url:
                        attachments, _advertised, ok = _scrape_documents(
                            session, download_url, folder)
                        if not ok:
                            tender_code_result = common.TENDER_PARTIAL
                    else:
                        reporting.documents_line(log, 0)

                    tenders.append({"title": fields.get("title"), "folder": folder,
                                     "attachments": attachments,
                                     "source_url": url, **fields})
                    tender_codes.add(tender_code_result)
                    all_codes.append(tender_code_result)
                    reporting.tender_line(log, index, len(urls), tender_code,
                                          tender_code_result)
                except BotBlockedError as exc:
                    # Blocked partway through: keep what was already scraped,
                    # but report the block rather than a partial.
                    log.error("blocked at tender %d/%d: %s", index, len(urls), exc)
                    return common.SITE_BOT_BLOCKED, tenders
                except Exception as exc:
                    reporting.tender_failed(log, index, len(urls), url, exc)
                    tender_codes.add(common.TENDER_PARTIAL)
                    all_codes.append(common.TENDER_PARTIAL)
                    continue

            reporting.diagnose(log, all_codes, logged_in=True)
            return common.site_code_from(tender_codes), tenders

    except BotBlockedError as exc:
        log.error("blocked: %s", exc)
        return common.SITE_BOT_BLOCKED, tenders
    except Exception:
        log.exception("browser run crashed")
        return common.SITE_TOTAL_FAILURE, tenders


# Site-level codes worth another attempt with a fresh browser. A Cloudflare
# challenge is intermittent from Cloud Run, so it is retried; if every attempt
# hits it, it is reported as SITE_BOT_BLOCKED rather than worked around.
RETRYABLE_CODES = (
    common.SITE_TOTAL_FAILURE,
    common.SITE_LOGIN_FAILED,
    common.SITE_BOT_BLOCKED,
)


def _scrape_with_retries(limit: int = 0, output_dir: str = "tenders_data",
                         attempts: int = 3) -> tuple[int, list[dict]]:
    """
    Retry wrapper: each attempt opens a brand-new browser, since a flagged
    session can stay flagged. Only retries when nothing was scraped and the
    failure was a login/total failure.
    """
    code, tenders = common.SITE_TOTAL_FAILURE, []
    if not all(credentials()):
        log.error("ACT_USERNAME / ACT_PASSWORD are not set -- not opening a browser. "
                  "Check the job's env vars / Secret Manager mapping.")
        return common.SITE_LOGIN_FAILED, tenders
    for attempt in range(1, attempts + 1):
        log.info("browser attempt %d/%d", attempt, attempts)
        code, tenders = _run_once(limit, output_dir)
        if tenders or code not in RETRYABLE_CODES:
            return code, tenders
        log.warning("attempt %d/%d got %s with 0 tenders -- retrying in %ds",
                    attempt, attempts, reporting.code_name(code), 15 * attempt)
        time.sleep(15 * attempt)
    return code, tenders


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


@reporting.reported(SOURCE_ID.upper())
def run_scraper_via_browser(limit: int = 0, output_dir: str = "tenders_data",
                            attempts: int = 3) -> common.ScrapeResult:
    """
    The pipeline's entry point. Returns (error code, site name, tenders scraped)
    and nothing else -- the tenders themselves are left in output_dir.
    """
    code, tenders = _scrape_with_retries(limit, output_dir, attempts)
    return common.ScrapeResult(code, SOURCE_ID, len(tenders))


def main():
    reporting.configure_logging()
    run_scraper_via_browser()  # no cap; for a capped local run use: python manager.py --local


if __name__ == "__main__":
    main()