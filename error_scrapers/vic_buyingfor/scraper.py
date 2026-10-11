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

from error_scrapers import common, reporting

from dotenv import load_dotenv
load_dotenv()

log = reporting.site_logger("VIC_BUYINGFOR")

SOURCE_ID = "vic_buyingfor"
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

RECONNECT_SECONDS = 7
WAIT_TIMEOUT = 30
PAUSE_SECONDS = 2.0

# A whole-run retry (fresh browser) is for a browser that would not start or
# crashed, or for a Cloudflare challenge (intermittent from Cloud Run). This is
# the most attempts in total. A failed login is never retried: repeated bad
# logins can lock the account.
BROWSER_ATTEMPTS = 3

# Site-level codes worth another attempt with a fresh browser when nothing was
# scraped. If every attempt is blocked the run is reported as SITE_BOT_BLOCKED
# and nothing is worked around.
# The download has to start within DOWNLOAD_WAIT_SECONDS; once a partial file
# exists it gets until it stalls or the hard maximum. Not retried: another
# request to a Cloudflare-fronted site risks a block.
DOWNLOAD_WAIT_SECONDS = int(os.environ.get("VIC_DOWNLOAD_WAIT_SECONDS", "180"))
DOWNLOAD_STALL_SECONDS = int(os.environ.get("VIC_DOWNLOAD_STALL_SECONDS", "300"))
DOWNLOAD_HARD_MAX_SECONDS = int(os.environ.get("VIC_DOWNLOAD_HARD_MAX_SECONDS", "900"))

RETRYABLE_CODES = (common.SITE_TOTAL_FAILURE, common.SITE_BOT_BLOCKED)

# The document form on the "Download Now" page: a checkbox per document
# (all ticked by default) and one submit button that returns a single zip.
DOWNLOAD_FORM_SELECTOR = "form#spec"
DOCUMENT_CHECKBOX_SELECTOR = "input[name='ids[]']"

# Signed-in pages carry a Log Out link; anonymous ones carry Log In.
SIGNED_IN_SELECTOR = "a[href='/logout']"

# A session that lapses mid-run is replaced by signing in again, this many
# times at most -- a login that keeps failing is a real problem, not a blip.
MAX_RELOGINS = 3


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


def is_signed_in(html: str) -> bool:
    """True if the page was served to a signed-in user."""
    return BeautifulSoup(html, "html.parser").select_one(SIGNED_IN_SELECTOR) is not None


def parse_download_form(html: str) -> dict | None:
    """
    The document form's state: {"count": documents offered, "unchecked": how many
    are not ticked}, or None if the page has no form (the layout changed, or
    we are not signed in).
    """
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one(DOWNLOAD_FORM_SELECTOR) is None:
        return None
    boxes = soup.select(f"{DOWNLOAD_FORM_SELECTOR} {DOCUMENT_CHECKBOX_SELECTOR}")
    return {"count": len(boxes), "unchecked": sum(1 for b in boxes if not b.has_attr("checked"))}


def find_download_docs_url(html: str, base_url: str = BASE_URL) -> str | None:
    """The signed-in "Download Now" link to the document form, or None."""
    for anchor in BeautifulSoup(html, "html.parser").select("a[href*='downloadSpecDocs']"):
        if "download" in anchor.get_text(" ", strip=True).lower():
            return urljoin(base_url, anchor["href"])
    return None


def format_detail_text(fields: dict, entry: dict, documents: list[dict]) -> str:
    """Render the tender as its page-text file."""
    lines = [
        "=" * 80,
        f"BUYING FOR VICTORIA: {fields.get('title') or fields.get('tender_code')}",
        "=" * 80,
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
            options["chromium_arg"] = "disable-dev-shm-usage,disable-gpu"
        self._sb_cm = SB(**options)
        self.sb = self._sb_cm.__enter__()
        try:
            log.info("chrome %s", self.sb.driver.capabilities.get("browserVersion"))
        except Exception:
            pass  # only for the logs; never worth failing the run
        # SeleniumBase saves clicked downloads into ./downloaded_files, a
        # fixed location with no SB() option to move it.
        self._downloads_dir = os.path.join(os.getcwd(), "downloaded_files")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._sb_cm is not None:
            self._sb_cm.__exit__(exc_type, exc_val, exc_tb)

    def _debug_dump(self, label: str) -> None:
        """Log what the browser is looking at, flattened to one line for Cloud Logging."""
        try:
            log.warning("debug %s title: %s", label, self.sb.get_title())
            log.warning("debug %s url: %s", label, self.sb.get_current_url())
            html = self.sb.get_page_source()[:1500].replace("\n", " ").replace("\r", " ")
            log.warning("debug %s html: %s", label, html)
        except Exception as e:
            log.warning("debug %s could not read page: %s", label, e)

    def get(self, url: str, wait_selector: str | None = None) -> str:
        """
        Load url once and return its HTML. If Cloudflare serves its challenge
        instead, log what the page looked like and raise BotBlockedError at
        once: reloading in the same browser does not clear it and only adds
        load to the site. A page that loads but never shows wait_selector is
        returned as-is, so the parser can report it as a structure change.
        """
        self.sb.uc_open_with_reconnect(url, reconnect_time=RECONNECT_SECONDS)
        if wait_selector:
            try:
                self.sb.wait_for_element(wait_selector, timeout=WAIT_TIMEOUT)
            except Exception:
                pass
        html = self.sb.get_page_source()
        if is_blocked(self.sb.get_title(), html):
            self._debug_dump("challenge")
            raise BotBlockedError(f"Cloudflare challenge instead of {url}")
        return html

    def login(self) -> bool:
        """
        Fill and submit the supplier login form. True once the page shows the
        Log Out link; False if the site says the username/password is wrong
        or nothing signs us in within ten seconds.
        """
        username, password = credentials()
        self.get(LOGIN_URL, wait_selector="#supplierUsername")
        self.sb.type("#supplierUsername", username)
        self.sb.type("#supplierPassword", password)
        self.sb.click("#supplierLoginForm button[type='submit']")
        for _ in range(10):
            time.sleep(1)
            if LOGIN_ERROR_TEXT in self.sb.get_page_source():
                log.error("login: the site says %r", LOGIN_ERROR_TEXT)
                return False
            if self.sb.is_element_present(SIGNED_IN_SELECTOR):
                return True
        return False

    def download_documents(self, download_docs_url: str, folder: str) -> str:
        """
        Open the document form, click Download, and move the zip that lands
        in SeleniumBase's downloads folder into `folder`. Returns its path.
        """
        html = self.get(download_docs_url, wait_selector="#downloadButton")
        form = parse_download_form(html)
        if form is None:
            raise common.StructureChangedError(
                f"no document form ({DOWNLOAD_FORM_SELECTOR}) on {download_docs_url}")
        if form["count"] == 0:
            raise common.StructureChangedError(f"the document form lists no documents on {download_docs_url}")
        if form["unchecked"]:
            # Every box is ticked by default; if that changes, Select All restores it.
            self.sb.click("#checkAll")
        os.makedirs(self._downloads_dir, exist_ok=True)
        before = set(os.listdir(self._downloads_dir))
        self.sb.click("#downloadButton")
        name = common.wait_for_download(
            self._downloads_dir, before, DOWNLOAD_WAIT_SECONDS,
            stall_seconds=DOWNLOAD_STALL_SECONDS, hard_max=DOWNLOAD_HARD_MAX_SECONDS)
        if name:
            target = os.path.join(folder, name)
            shutil.move(os.path.join(self._downloads_dir, name), target)
            return target
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
    common.save_page_text(folder, tender_code, format_detail_text(fields, entry, documents))
    common.add_source_url(folder, tender_code, url)

    tender = {
        "tender_id": tender_code,
        "title": fields.get("title"),
        "folder": folder,
        "attachments": [],
        "source_url": url,
        "documents_gated": False,
    }
    if not documents:
        reporting.documents_line(log, 0, 0)
        return common.SITE_SUCCESS, tender

    download_url = find_download_docs_url(html)
    if download_url is None:
        tender["documents_gated"] = documents_require_login(html) or not is_signed_in(html)
        log.warning("       documents: %d advertised, 0 downloaded -- %s", len(documents),
                    "not signed in" if tender["documents_gated"]
                    else "signed in but the page offers no download link")
        return common.TENDER_PARTIAL, tender

    zip_path = None
    try:
        zip_path = session.download_documents(download_url, folder)
        tender["attachments"], any_failed = common.unpack_zip(zip_path, folder)
    except BotBlockedError:
        raise  # a Cloudflare challenge ends the run -- it is not a per-tender failure
    except Exception as e:
        log.warning("       document download FAILED for %s: %s", tender_code, e)
        return common.TENDER_PARTIAL, tender
    finally:
        if zip_path and os.path.exists(zip_path):
            os.remove(zip_path)

    reporting.documents_line(log, len(tender["attachments"]), len(documents))
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


def _scrape_site(limit: int = 0, output_dir: str = "tenders_data",
                 headless: bool = True) -> tuple[int, list[dict]]:
    """
    Scrape every open Victorian tender. `limit` of 0 means every tender found.

    Returns (site_code, tenders). A failed login with credentials set is
    SITE_LOGIN_FAILED, but the page text is still scraped.
    """
    username, password = credentials()
    # Say plainly whether the secrets reached this process. Values are never logged.
    log.info("login: VIC_USERNAME %s, VIC_PASSWORD %s",
             "set" if username else "MISSING", "set" if password else "MISSING")
    tenders: list[dict] = []
    logged_in = False
    login_failed = False
    try:
        with BrowserSession(headless=headless) as session:
            if username and password:
                logged_in = session.login()
                login_failed = not logged_in
                if logged_in:
                    log.info("login: ok")
                else:
                    log.error("login: FAILED -- continuing without documents")
            else:
                log.warning("login: no credentials -- documents will be skipped and "
                            "tenders with documents will be TENDER_PARTIAL")

            entries = collect_all_listing_entries(session, limit)
            log.info("listing: %d tender(s) found", len(entries))

            tender_codes = {common.SITE_LOGIN_FAILED} if login_failed else set()
            all_codes = []
            relogins = 0
            for index, entry in enumerate(entries, start=1):
                try:
                    code, tender = scrape_opportunity(session, entry, output_dir)
                    if tender and tender["documents_gated"] and logged_in and relogins < MAX_RELOGINS:
                        # We were signed in, so the session probably lapsed.
                        relogins += 1
                        log.warning("       documents refused while signed in -- signing in again "
                                    "(%d/%d) and retrying this tender", relogins, MAX_RELOGINS)
                        logged_in = session.login()
                        if logged_in:
                            code, tender = scrape_opportunity(session, entry, output_dir)
                        else:
                            log.error("login: FAILED on re-login")
                    if tender:
                        tenders.append(tender)
                    tender_codes.add(code)
                    all_codes.append(code)
                    reporting.tender_line(log, index, len(entries),
                                          tender.get("tender_id") or entry["url"], code)
                except BotBlockedError:
                    raise
                except Exception as exc:
                    reporting.tender_failed(log, index, len(entries), entry["url"], exc)
                    tender_codes.add(common.TENDER_PARTIAL)
                    all_codes.append(common.TENDER_PARTIAL)
                time.sleep(PAUSE_SECONDS)

            reporting.diagnose(log, all_codes, logged_in=logged_in)

        return common.site_code_from(tender_codes), tenders

    except BotBlockedError:
        log.error("Cloudflare challenge did not clear -- stopping the run")
        return common.SITE_BOT_BLOCKED, tenders
    except common.StructureChangedError as exc:
        log.error("structure change: %s", exc)
        return common.SITE_STRUCTURE_CHANGE, tenders
    except Exception as exc:
        log.error("run failed: %s: %s", type(exc).__name__, exc)
        return common.SITE_TOTAL_FAILURE, tenders


def _scrape_with_retries(limit: int = 0, output_dir: str = "tenders_data",
                         headless: bool = True,
                         attempts: int = BROWSER_ATTEMPTS) -> tuple[int, list[dict]]:
    """
    Retry, each time with a brand-new browser, only when nothing was scraped
    and the code is in RETRYABLE_CODES: the browser would not start or
    crashed, or Cloudflare served its challenge. At most `attempts` tries in
    total, waiting longer between each. A failed login, structure change or any
    partial result is returned as-is.
    """
    code, tenders = common.SITE_TOTAL_FAILURE, []
    for attempt in range(1, attempts + 1):
        log.info("browser attempt %d/%d", attempt, attempts)
        code, tenders = _scrape_site(limit, output_dir, headless)
        if tenders or code not in RETRYABLE_CODES:
            return code, tenders
        if attempt < attempts:
            log.warning("attempt %d/%d got %s with 0 tenders -- retrying in %ds",
                        attempt, attempts, reporting.code_name(code), 15 * attempt)
            time.sleep(15 * attempt)
    return code, tenders


@reporting.reported(SOURCE_ID.upper())
def run_scraper(limit: int = 0, output_dir: str = "tenders_data",
                headless: bool = True) -> common.ScrapeResult:
    """
    The pipeline's entry point. Returns (error code, site name, tenders scraped)
    and nothing else -- the tenders themselves are left in output_dir.
    """
    code, tenders = _scrape_with_retries(limit, output_dir, headless)
    return common.ScrapeResult(code, SOURCE_ID, len(tenders))


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scrape open Buying for Victoria tenders.")
    parser.add_argument("--visible", action="store_true", help="show the browser window")
    args = parser.parse_args()

    reporting.configure_logging()
    run_scraper(headless=not args.visible)  # no cap; for a capped local run use: python manager.py --local


if __name__ == "__main__":
    main()