"""
error_scrapers/vic_buyingfor/scraper.py

Scraper for Buying for Victoria (tenders.vic.gov.au). Same shape as
error_scrapers/grant_connect/scraper.py: every function here is covered by
test_cases/test_vic_buyingfor_scraper.py.

Per tender, saves into its own folder (via common.py's
tender_dir/save_page_text/unpack_zip):
    __tender__<RFx number>.txt -- the listing row plus the detail page's fields
    <attachment files>         -- the files from the tender's document zip
    <attachment>.txt           -- extracted text per attachment

Browser:
    The site sits behind Cloudflare, which answers any plain HTTP client
    with a 403 "Attention Required!" page, so every page is fetched through a
    SeleniumBase UC-mode Chrome (see BrowserSession). A Cloudflare challenge
    that survives every retry is SITE_BOT_BLOCKED.

    It is the same tender platform as Tenders ACT -- same #opportunityGeneral
    detail block, same a.tenderRowTitle listing rows, same "downloadSpecDocs"
    document form -- so this mirrors error_scrapers/tenders_act.

Documents:
    Every attachment's name, version and size is public, but the download
    form only exists for a signed-in supplier ("You must be logged in to
    download documents"). Set VIC_USERNAME / VIC_PASSWORD to download them.
    Without them, a tender with documents is TENDER_PARTIAL; with them, a
    failed login is SITE_LOGIN_FAILED and the page text is still scraped.
"""

import math
import os
import re
import shutil
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from error_scrapers import common

from dotenv import load_dotenv
load_dotenv()

SOURCE_ID = "vic-buyingfor"
BASE_URL = "https://www.tenders.vic.gov.au"
LOGIN_URL = f"{BASE_URL}/login"
LIST_URL = f"{BASE_URL}/tenders/open"

DETAIL_LINK_SELECTOR = "a.tenderRowTitle"

# The block each detail field (Type, Status, Number, UNSPSC, Region(s))
# lives in. If this is missing on a page Cloudflare didn't block, the site's
# structure has changed -- the structure-changed fixture renames exactly
# this id.
FIELD_SECTION_SELECTOR = "#opportunityGeneral"

# Every attachment in the "Specification Documents" block.
SPEC_DOC_SELECTOR = "#specsList li.specDoc"

LOGIN_REQUIRED_TEXT = "must be logged in to download"
LOGIN_ERROR_TEXT = "Invalid username/password combination"

GET_ATTEMPTS = 3
WAIT_TIMEOUT = 30
PAUSE_SECONDS = 2.0


def credentials():
    """Return (username, password) from the environment, or (None, None)."""
    return (
        os.environ.get("VIC_USERNAME") or None,
        os.environ.get("VIC_PASSWORD") or None,
    )


class BotBlockedError(Exception):
    """Raised when Cloudflare keeps serving its challenge instead of the page."""


def is_blocked(title: str, html: str) -> bool:
    """True if what loaded is a Cloudflare challenge or block page, not the site."""
    title = (title or "").lower()
    if "attention required" in title or "just a moment" in title:
        return True
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True).lower()
    return "you have been blocked" in text or "verify you are human" in text


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def _text(element, selector):
    found = element.select_one(selector)
    return re.sub(r"\s+", " ", found.get_text(" ", strip=True)) if found else None


def parse_listing(html: str, base_url: str = BASE_URL) -> list[dict]:
    """
    Return one entry per tender row on a listing page.

    The row carries the opening and closing dates, which the detail page
    doesn't repeat -- so the row is kept, not just its link.
    """
    soup = BeautifulSoup(html, "html.parser")
    entries, seen = [], set()
    for row in soup.select("tr"):
        anchor = row.select_one(DETAIL_LINK_SELECTOR)
        if anchor is None or not anchor.get("href"):
            continue
        url = urljoin(base_url, anchor["href"])
        if url in seen:
            continue
        seen.add(url)
        entries.append({
            "url": url,
            # The cell's first <b> is the column label shown on mobile.
            "rfx": _text(row, "td.tender-code-state .tablesaw-cell-content b"),
            "title": anchor.get_text(" ", strip=True),
            "opening_date": _text(row, "span.opening_date"),
            "closing_date": _text(row, "span.closing_date"),
        })
    return entries


def total_pages(html: str) -> int | None:
    """Page count from the pager's "Records: 1 - 25 of 78", or None if absent."""
    pager = BeautifulSoup(html, "html.parser").select_one("div.paging")
    match = re.search(r"(\d+)\s*-\s*(\d+)\s+of\s+(\d+)", pager.get_text(" ", strip=True)) if pager else None
    if not match:
        return None
    first, last, total = (int(g) for g in match.groups())
    return max(math.ceil(total / max(last - first + 1, 1)), 1)


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def parse_detail(html: str) -> tuple[dict, int]:
    """
    Pull a tender's own fields off its detail page.

    Returns (fields, status_code). If the field section isn't found on an
    otherwise real page, returns ({}, SITE_STRUCTURE_CHANGE) rather than
    guessing at a different layout.
    """
    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one(FIELD_SECTION_SELECTOR)
    if section is None:
        return {}, common.SITE_STRUCTURE_CHANGE

    fields = {"title": _text(soup, "#tenderTitle")}

    # "Issued By <agency>" sits in a bold block in the header.
    fields["agency"] = None
    for block in soup.select("#opportunityHeader .weight-bold"):
        lines = [line for line in block.get_text("\n", strip=True).splitlines() if line.strip()]
        if lines and lines[0].lower().startswith("issued by") and len(lines) > 1:
            fields["agency"] = lines[-1].strip()

    for row in section.select(".row"):
        cells = row.find_all("div", recursive=False)
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).rstrip(":")
            if label:
                fields[label] = cells[1].get_text(" ", strip=True)

    fields["tender_type"] = fields.pop("Type", None)
    fields["tender_status"] = fields.pop("Status", None)
    fields["tender_code"] = fields.pop("Number", None)
    if not fields["tender_code"]:
        return {}, common.SITE_STRUCTURE_CHANGE

    description = soup.select_one("#tenderDescription")
    if description is not None:
        fields["description"] = description.get_text("\n", strip=True).replace(
            "Description", "", 1
        ).strip()

    contact = soup.select_one("#opportunityContacts div.contact")
    if contact is not None:
        items = [li.get_text(" ", strip=True) for li in contact.select("li")]
        if items:
            fields["contact_name"] = re.sub(r"\s+", " ", items[0].replace("(Enquiries)", "")).strip()
        mail = contact.select_one("a[href^='mailto:']")
        if mail is not None:
            fields["contact_email"] = mail["href"].split(":", 1)[1].strip()

    return fields, common.SITE_SUCCESS


def parse_documents(html: str) -> list[dict]:
    """
    Every attachment in the Specification Documents block, as
    {file_name, version, size} -- listed even when it can't be downloaded,
    so the page text records what the tender is missing.
    """
    soup = BeautifulSoup(html, "html.parser")
    documents = []
    for item in soup.select(SPEC_DOC_SELECTOR):
        name = _text(item, "span.specName")
        if not name:
            continue
        version = size = None
        for line in item.select("ul li"):
            text = re.sub(r"\s+", " ", line.get_text(" ", strip=True))
            if text.lower().startswith("version"):
                version = text
            match = re.search(r"\(([\d.,]+\s*[KMG]?B)\)", text, re.I)
            if match:
                size = match.group(1)
        documents.append({"file_name": common.sanitise_filename(name), "version": version, "size": size})
    return documents


def documents_require_login(html: str) -> bool:
    """True if the documents block says a login is needed to download."""
    return LOGIN_REQUIRED_TEXT in BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()


def find_download_docs_url(html: str, base_url: str = BASE_URL) -> str | None:
    """The signed-in "Download Now" link to the document form, or None."""
    for anchor in BeautifulSoup(html, "html.parser").select("a[href*='downloadSpecDocs']"):
        if "download" in anchor.get_text(" ", strip=True).lower():
            return urljoin(base_url, anchor["href"])
    return None


def format_detail_text(fields: dict, entry: dict, documents: list[dict], url: str) -> str:
    """Render the tender as its page-text file."""
    lines = [
        "=" * 80,
        f"BUYING FOR VICTORIA: {fields.get('title') or fields.get('tender_code')}",
        "=" * 80,
        "",
        f"Detail URL: {url}",
        "",
    ]
    for key, value in fields.items():
        if value and key != "description":
            lines.append(f"{key}: {value}")
    # Only the listing row carries the dates.
    for key in ("opening_date", "closing_date"):
        if entry.get(key):
            lines.append(f"{key}: {entry[key]}")
    if fields.get("description"):
        lines += ["", "DESCRIPTION:", "-" * 40, fields["description"]]
    if documents:
        lines += ["", "SPECIFICATION DOCUMENTS:", "-" * 40]
        for doc in documents:
            extra = ", ".join(v for v in (doc.get("version"), doc.get("size")) if v)
            lines.append(f"{doc['file_name']}" + (f" ({extra})" if extra else ""))
    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

class BrowserSession:
    """
    One SeleniumBase UC-mode Chrome for the whole run. Use as a context
    manager so the browser always closes:

        with BrowserSession() as session:
            html = session.get(LIST_URL, wait_selector=DETAIL_LINK_SELECTOR)
    """

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._sb_cm = None
        self.sb = None

    def __enter__(self):
        from seleniumbase import SB

        options = {"uc": True, "headless": self._headless}
        if os.environ.get("RUNNING_IN_CONTAINER", "").lower() in ("1", "true", "yes"):
            # Chrome can't run as root without --no-sandbox, and needs its
            # shared memory off the container's tiny /dev/shm.
            options["no_sandbox"] = True
            options["chromium_arg"] = "disable-dev-shm-usage,disable-gpu"
        self._sb_cm = SB(**options)
        self.sb = self._sb_cm.__enter__()
        # SeleniumBase saves clicked downloads into ./downloaded_files, a
        # fixed location with no SB() option to move it.
        self._downloads_dir = os.path.join(os.getcwd(), "downloaded_files")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._sb_cm is not None:
            self._sb_cm.__exit__(exc_type, exc_val, exc_tb)

    def get(self, url: str, wait_selector: str | None = None) -> str:
        """
        Load url and return its HTML. Retries a Cloudflare challenge with a
        longer reconnect each time, raising BotBlockedError if it never
        clears. A page that loads but never shows wait_selector is returned
        as-is, so the parser can report it as a structure change.
        """
        html = ""
        for attempt in range(1, GET_ATTEMPTS + 1):
            self.sb.uc_open_with_reconnect(url, reconnect_time=4 + 3 * attempt)
            if wait_selector:
                try:
                    self.sb.wait_for_element(wait_selector, timeout=WAIT_TIMEOUT)
                except Exception:
                    pass
            html = self.sb.get_page_source()
            if not is_blocked(self.sb.get_title(), html):
                return html
            print(f"[VIC] Cloudflare challenge on {url} (attempt {attempt}/{GET_ATTEMPTS})", flush=True)
        raise BotBlockedError(f"Cloudflare kept blocking {url}")

    def login(self) -> bool:
        """Fill and submit the supplier login form. Returns True on success."""
        username, password = credentials()
        self.get(LOGIN_URL, wait_selector="#supplierUsername")
        self.sb.type("#supplierUsername", username)
        self.sb.type("#supplierPassword", password)
        self.sb.click("#supplierLoginForm button[type='submit']")
        for _ in range(10):
            time.sleep(1)
            if LOGIN_ERROR_TEXT in self.sb.get_page_source():
                return False
            if not self.sb.is_element_visible("#supplierLoginForm"):
                return True
        return "/login" not in self.sb.get_current_url()

    def download_documents(self, download_docs_url: str, folder: str) -> str:
        """
        Open the document form, click Download, and move the zip that lands
        in SeleniumBase's downloads folder into `folder`. Returns its path.
        """
        self.get(download_docs_url, wait_selector="#downloadButton")
        os.makedirs(self._downloads_dir, exist_ok=True)
        before = set(os.listdir(self._downloads_dir))
        self.sb.click("#downloadButton")
        for _ in range(120):
            new = [f for f in set(os.listdir(self._downloads_dir)) - before
                   if not f.endswith(".crdownload")]
            if new:
                target = os.path.join(folder, new[0])
                shutil.move(os.path.join(self._downloads_dir, new[0]), target)
                return target
            time.sleep(1)
        raise TimeoutError(f"Document download from {download_docs_url} did not finish")


# ---------------------------------------------------------------------------
# Per-tender and full-run orchestration
# ---------------------------------------------------------------------------

def scrape_opportunity(session, entry: dict, output_dir: str = "tenders_data") -> tuple[int, dict]:
    """
    Scrape one tender (a listing entry) into its own folder.

    Returns (status_code, tender). `tender` is empty if the page could not be
    parsed. `documents_gated` on the tender records that the documents were
    listed but no download form was offered.
    """
    url = entry["url"]
    html = session.get(url, wait_selector=FIELD_SECTION_SELECTOR)
    fields, code = parse_detail(html)
    if code != common.SITE_SUCCESS:
        return code, {}

    tender_code = fields["tender_code"]
    folder = common.tender_dir(tender_code, output_dir)
    documents = parse_documents(html)
    common.save_page_text(folder, tender_code, format_detail_text(fields, entry, documents, url))

    tender = {
        "tender_id": tender_code,
        "title": fields.get("title"),
        "folder": folder,
        "attachments": [],
        "source_url": url,
        "documents_gated": False,
    }
    if not documents:
        return common.SITE_SUCCESS, tender

    download_url = find_download_docs_url(html)
    if download_url is None:
        tender["documents_gated"] = documents_require_login(html)
        return common.TENDER_PARTIAL, tender

    zip_path = None
    try:
        zip_path = session.download_documents(download_url, folder)
        tender["attachments"], any_failed = common.unpack_zip(zip_path, folder)
    except Exception as e:
        print(f"[VIC] document download failed for {tender_code}: {e}", flush=True)
        return common.TENDER_PARTIAL, tender
    finally:
        if zip_path and os.path.exists(zip_path):
            os.remove(zip_path)

    return (common.TENDER_PARTIAL if any_failed else common.SITE_SUCCESS), tender


def collect_all_listing_entries(session, limit: int = 0) -> list[dict]:
    """
    Walk every page of open tenders, stopping at the pager's page count, at
    an empty page, or once `limit` is reached. `limit` of 0 means every
    open tender.

    Raises StructureChangedError if the first page has no tender rows: the
    portal always has open tenders, so that means the markup changed.
    """
    entries, page, pages = [], 1, None
    while True:
        url = LIST_URL if page == 1 else f"{LIST_URL}?page={page}"
        html = session.get(url, wait_selector=DETAIL_LINK_SELECTOR)
        if pages is None:
            pages = total_pages(html)

        known = {e["url"] for e in entries}
        new = [e for e in parse_listing(html) if e["url"] not in known]
        if not new:
            if page == 1:
                raise common.StructureChangedError("No tender rows on the first listing page.")
            break
        entries.extend(new)
        if limit and len(entries) >= limit:
            return entries[:limit]
        if pages is not None and page >= pages:
            break
        page += 1
        time.sleep(PAUSE_SECONDS)
    return entries


def run_scraper(limit: int = 0, output_dir: str = "tenders_data",
                headless: bool = True) -> tuple[int, list[dict]]:
    """
    Scrape every open Victorian tender. `limit` of 0 means every tender found.

    Returns (site_code, tenders). A failed login with credentials set is
    SITE_LOGIN_FAILED, but the page text is still scraped.
    """
    username, password = credentials()
    tenders: list[dict] = []
    login_failed = False
    try:
        with BrowserSession(headless=headless) as session:
            if username and password:
                login_failed = not session.login()
                if login_failed:
                    print("[VIC] login failed -- continuing without documents", flush=True)
            else:
                print("[VIC] VIC_USERNAME/VIC_PASSWORD not set -- documents will be skipped", flush=True)

            entries = collect_all_listing_entries(session, limit)

            tender_codes = {common.SITE_LOGIN_FAILED} if login_failed else set()
            for entry in entries:
                try:
                    code, tender = scrape_opportunity(session, entry, output_dir)
                    if tender:
                        tenders.append(tender)
                    tender_codes.add(code)
                except BotBlockedError:
                    raise
                except Exception as e:
                    print(f"[VIC] tender failed: {entry['url']}: {e}", flush=True)
                    tender_codes.add(common.TENDER_PARTIAL)
                time.sleep(PAUSE_SECONDS)

        return common.site_code_from(tender_codes), tenders

    except BotBlockedError:
        return common.SITE_BOT_BLOCKED, tenders
    except common.StructureChangedError:
        return common.SITE_STRUCTURE_CHANGE, tenders
    except Exception as e:
        print(f"[VIC] run failed: {type(e).__name__}: {e}", flush=True)
        return common.SITE_TOTAL_FAILURE, tenders


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scrape open Buying for Victoria tenders.")
    parser.add_argument("--limit", type=int, default=5, help="max tenders (0 = all)")
    parser.add_argument("--output-dir", default="tenders_data")
    parser.add_argument("--visible", action="store_true", help="show the browser window")
    args = parser.parse_args()

    code, tenders = run_scraper(limit=args.limit, output_dir=args.output_dir,
                                headless=not args.visible)
    print(f"Buying for Victoria run finished with code {code}, {len(tenders)} tenders scraped")


if __name__ == "__main__":
    main()
