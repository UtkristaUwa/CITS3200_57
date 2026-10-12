"""
error_scrapers/sa_tenders/scraper.py

Scraper for SA Tenders and Contracts (tenders.sa.gov.au). Every function here is
covered by test_cases/test_sa_tenders.py.

It is the same Consolidated Tenders platform as Buying for Victoria and Tenders
ACT: the same #opportunityGeneral detail block, the same a.tenderRowTitle rows
and the same "downloadSpecDocs" document form. This mirrors
error_scrapers/vic_buyingfor/scraper.py.

Per tender, saves into its own folder (via common.py's
tender_dir/save_page_text/unpack_zip):
    __tender__<Tender Code>.txt -- the listing row plus the detail page's fields,
                                   then a FULL PAGE TEXT block
    <attachment files>          -- the files from the tender's document zip
    <attachment>.txt            -- extracted text per attachment

Listing:
    Tenders > Current is /tender/search?preset=open. Its results are not in the
    page: the page loads them by AJAX (getGroupedTableAjax) as soon as it opens,
    15 to a page, with a pager ("Records: 1 - 15 of 117"). Page 1 is read from
    the browser once it appears; each later page is requested by calling the
    site's own pager function. The run logs an error if fewer tenders turn up
    than the pager says exist.

Browser:
    The site sits behind Cloudflare, so pages are fetched through a SeleniumBase
    UC-mode Chrome (see BrowserSession). A Cloudflare challenge that survives
    every retry is SITE_BOT_BLOCKED.

Documents:
    The document form only exists for a signed-in supplier. Set SA_USERNAME /
    SA_PASSWORD. Without them, a tender with documents is TENDER_PARTIAL; with
    them, a failed login is SITE_LOGIN_FAILED and the page text is still scraped.
"""

import os
import re
import shutil
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from error_scrapers import common, reporting, run_notes

from dotenv import load_dotenv
load_dotenv()

log = reporting.site_logger("SA_TENDERS")

SOURCE_ID = "sa_tenders"
BASE_URL = "https://www.tenders.sa.gov.au"
LOGIN_URL = f"{BASE_URL}/login/"
LIST_URL = f"{BASE_URL}/tender/search?preset=open"

DETAIL_LINK_SELECTOR = "a.tenderRowTitle"

# The block each detail field (Type, Tender State, Tender Code, Category, ...)
# lives in. If this is missing on a page Cloudflare didn't block, the site's
# structure has changed -- the structure-changed test renames exactly this id.
FIELD_SECTION_SELECTOR = "#opportunityGeneral"
# The content area of a details page (leaves out the site menu in the full page text)
PAGE_CONTENT_SELECTOR = "main"

# Every attachment in the "Specification Documents" block.
SPEC_DOC_SELECTOR = "#specsList li.specDoc"

LOGIN_REQUIRED_TEXT = "must be logged in to download"
LOGIN_ERROR_TEXT = "Invalid username/password combination"

RECONNECT_SECONDS = 7
WAIT_TIMEOUT = 30
POLL_SECONDS = 0.5
PAUSE_SECONDS = 2.0

# A whole-run retry (fresh browser) is for a browser that would not start or
# crashed, or for a Cloudflare challenge. A failed login is never retried:
# repeated bad logins can lock the account.
BROWSER_ATTEMPTS = 3
RETRYABLE_CODES = (common.SITE_TOTAL_FAILURE, common.SITE_BOT_BLOCKED)

# The download has to start within DOWNLOAD_WAIT_SECONDS; once a partial file
# exists it gets until it stalls or the hard maximum.
DOWNLOAD_WAIT_SECONDS = int(os.environ.get("SA_DOWNLOAD_WAIT_SECONDS", "480"))
DOWNLOAD_STALL_SECONDS = int(os.environ.get("SA_DOWNLOAD_STALL_SECONDS", "480"))
DOWNLOAD_HARD_MAX_SECONDS = int(os.environ.get("SA_DOWNLOAD_HARD_MAX_SECONDS", "1800"))

DOWNLOAD_FORM_SELECTOR = "form#spec"
DOCUMENT_CHECKBOX_SELECTOR = "input[name='ids[]']"

# Signed-in pages carry a Log Out link; anonymous ones carry Log In.
SIGNED_IN_SELECTOR = "a[href='/logout']"

# A session that lapses mid-run is replaced by signing in again, this many
# times at most.
MAX_RELOGINS = 3


def credentials():
    """Return (username, password) from the environment, or (None, None)."""
    return (
        os.environ.get("SA_USERNAME") or None,
        os.environ.get("SA_PASSWORD") or None,
    )


class BotBlockedError(Exception):
    """Raised when Cloudflare keeps serving its challenge instead of the page."""


class BrowserLostError(Exception):
    """Raised when the browser itself has died (crashed, closed, lost its session)."""


# Fragments of the messages Selenium/Chrome give when the browser is gone, not
# when one page misbehaved. Matched on text so this file needs no selenium import.
_BROWSER_GONE_MARKERS = (
    "invalid session id",
    "session deleted",
    "chrome not reachable",
    "disconnected",
    "no such window",
    "target window already closed",
    "web view not found",
    "not connected to devtools",
    "browser has closed",
    "connection refused",
    "max retries exceeded",
)


def browser_is_gone(exc: BaseException) -> bool:
    """True if `exc` means the browser died, so every later page would fail too."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _BROWSER_GONE_MARKERS)


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
    Return one entry per tender row in the results.

    The row carries the closing date and the category, which the detail page
    doesn't always repeat -- so the row is kept, not just its link. A link
    that is not inside a table row is still returned (with the link's text
    only), so a layout change in the row does not lose the tender.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    entries, seen = [], set()
    for anchor in soup.select(DETAIL_LINK_SELECTOR):
        href = anchor.get("href")
        if not href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        row = anchor.find_parent("tr") or anchor.parent
        issued = None
        categories = []
        for detail in row.select("span.line-item-detail"):
            line = re.sub(r"\s+", " ", detail.get_text(" ", strip=True))
            if line.lower().startswith("issued by"):
                issued = line.split(":", 1)[-1].strip()
            elif line.lower().startswith("category"):
                # "Category: X" or, with several, "Category 2: Y - 20%"
                categories.append(line.split(":", 1)[-1].strip())
        category = "; ".join(c for c in categories if c) or None
        code_el = row.select_one("td b") if row.name == "tr" else None
        entries.append({
            "url": url,
            "tender_code": re.sub(r"\s+", " ", code_el.get_text(" ", strip=True)) if code_el else None,
            "title": re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)),
            "state": _text(row, "span.tender-row-state"),
            "agency": issued,
            "category": category,
            "closing_date": _text(row, "span.closing_date"),
        })
    return entries


def listing_counts(html: str) -> tuple[int, int] | None:
    """(rows per page, total tenders) from the pager's "Records: 1 - 15 of 117",
    or None if the page has no pager (one page of results has none)."""
    pager = BeautifulSoup(html or "", "html.parser").select_one("div.paging")
    match = re.search(r"Records:\s*(\d+)\s*-\s*(\d+)\s+of\s+(\d+)",
                      pager.get_text(" ", strip=True)) if pager else None
    if not match:
        return None
    first, last, total = (int(g) for g in match.groups())
    return max(last - first + 1, 1), total


def total_pages(html: str) -> int:
    """How many result pages there are (1 when the page has no pager)."""
    counts = listing_counts(html)
    if counts is None:
        return 1
    per_page, total = counts
    return max(-(-total // per_page), 1)


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def decode_cf_email(encoded: str) -> str | None:
    """Cloudflare hides email addresses as a hex string: the first byte is the
    key and every following byte is XORed with it."""
    try:
        data = bytes.fromhex(encoded)
        key = data[0]
        return bytes(b ^ key for b in data[1:]).decode("utf-8")
    except (ValueError, IndexError, UnicodeDecodeError):
        return None


def _contact_email(contact) -> str | None:
    hidden = contact.select_one("[data-cfemail]")
    if hidden is not None:
        return decode_cf_email(hidden["data-cfemail"])
    mail = contact.select_one("a[href^='mailto:']")
    if mail is not None:
        return mail["href"].split(":", 1)[1].strip()
    return None


def parse_detail(html: str) -> tuple[dict, int]:
    """
    Pull a tender's own fields off its detail page.

    Returns (fields, status_code). If the field section isn't found on an
    otherwise real page, returns ({}, SITE_STRUCTURE_CHANGE) rather than
    guessing at a different layout.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    section = soup.select_one(FIELD_SECTION_SELECTOR)
    if section is None:
        return {}, common.SITE_STRUCTURE_CHANGE

    fields = {"title": _text(soup, "#tenderTitle")}

    # "Issued By <agency>" sits in a bold block in the header.
    fields["agency"] = None
    for block in soup.select("#opportunityHeader .weight-bold"):
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
        if text.lower().startswith("issued by"):
            fields["agency"] = text[len("issued by"):].strip() or None

    for row in section.select(".row"):
        cells = row.find_all("div", recursive=False)
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).rstrip(":")
            if label:
                fields[label] = re.sub(r"\s+", " ", cells[1].get_text(" ", strip=True))

    fields["tender_type"] = fields.pop("Type", None)
    fields["tender_status"] = fields.pop("Tender State", None)
    fields["tender_code"] = fields.pop("Tender Code", None)
    if not fields["tender_code"]:
        return {}, common.SITE_STRUCTURE_CHANGE

    description = soup.select_one("#tenderDescription")
    if description is not None:
        fields["description"] = description.get_text("\n", strip=True).replace(
            "Description", "", 1
        ).strip()

    closing = soup.select_one("#tenderClosingTime")
    if closing is not None:
        fields["closing_date"] = re.sub(
            r"\s+", " ", closing.get_text(" ", strip=True).replace("Tender closes at", "")).strip()

    contacts = []
    for contact in soup.select("#opportunityContacts div.contact"):
        items = [li.get_text(" ", strip=True) for li in contact.select("li")]
        name = re.sub(r"\s+", " ", items[0].replace("(Enquiries)", "")).strip() if items else ""
        email = _contact_email(contact)
        if name or email:
            contacts.append({"name": name, "email": email})
    if contacts:
        fields["contact_name"] = contacts[0]["name"]
        if contacts[0]["email"]:
            fields["contact_email"] = contacts[0]["email"]
        if len(contacts) > 1:
            fields["other_contacts"] = "; ".join(
                f"{c['name']} <{c['email']}>" if c["email"] else c["name"] for c in contacts[1:])

    return fields, common.SITE_SUCCESS


def parse_documents(html: str) -> list[dict]:
    """
    Every attachment in the Specification Documents block, as
    {file_name, version, size} -- listed even when it can't be downloaded,
    so the page text records what the tender is missing.
    """
    soup = BeautifulSoup(html or "", "html.parser")
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
    return LOGIN_REQUIRED_TEXT in BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True).lower()


def is_signed_in(html: str) -> bool:
    """True if the page was served to a signed-in user."""
    return BeautifulSoup(html or "", "html.parser").select_one(SIGNED_IN_SELECTOR) is not None


def login_failed(html: str) -> bool:
    """True if the page shows the "wrong username/password" error."""
    return LOGIN_ERROR_TEXT in (html or "")


def parse_download_form(html: str) -> dict | None:
    """
    The document form's state: {"count": documents offered, "unchecked": how many
    are not ticked}, or None if the page has no form.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    if soup.select_one(DOWNLOAD_FORM_SELECTOR) is None:
        return None
    boxes = soup.select(f"{DOWNLOAD_FORM_SELECTOR} {DOCUMENT_CHECKBOX_SELECTOR}")
    return {"count": len(boxes), "unchecked": sum(1 for b in boxes if not b.has_attr("checked"))}


def find_download_docs_url(html: str, base_url: str = BASE_URL) -> str | None:
    """The signed-in "Download Now" link to the document form, or None."""
    for anchor in BeautifulSoup(html or "", "html.parser").select("a[href*='downloadSpecDocs']"):
        if "download" in anchor.get_text(" ", strip=True).lower():
            return urljoin(base_url, anchor["href"])
    return None


def format_detail_text(fields: dict, entry: dict, documents: list[dict]) -> str:
    """Render the tender as its page-text file."""
    lines = [
        "=" * 80,
        f"SA TENDERS: {fields.get('title') or fields.get('tender_code')}",
        "=" * 80,
        "",
    ]
    for key, value in fields.items():
        if value and key != "description":
            lines.append(f"{key}: {value}")
    # The listing row lists every category with its share ("A - 20%; B - 20%"),
    # which the detail page does not; keep it when it says something more.
    if entry.get("category") and entry["category"] != fields.get("Category"):
        lines.append(f"listing_categories: {entry['category']}")
    if entry.get("closing_date") and not fields.get("closing_date"):
        lines.append(f"closing_date: {entry['closing_date']}")
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
        Load url and return its HTML. A load that fails is loaded again, up to
        common.PAGE_LOAD_ATTEMPTS times. If Cloudflare serves its challenge
        instead, log what the page looked like and raise BotBlockedError at
        once. A page that loads but never shows wait_selector is returned
        as-is, so the parser can report it as a structure change.
        """
        common.load_page_with_retries(
            lambda: self.sb.uc_open_with_reconnect(url, reconnect_time=RECONNECT_SECONDS), url)
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

    def open_page(self, page: int) -> str:
        """
        Show page `page` of the open-tenders results (the same call the pager
        links make) and return the page's HTML once the new rows have arrived.

        The pager form lives inside #resultDiv, and the site swaps the whole
        block when its answer comes back. So #resultDiv must not be emptied
        first: that deletes the form, and the call then fails with "Cannot read
        properties of undefined (reading 'page')". The new page is the one whose
        rows differ from the rows shown now. If they never do, the HTML is
        returned as it is after WAIT_TIMEOUT, and the caller sees a page with
        nothing new.
        """
        page = int(page)
        before = [e["url"] for e in parse_listing(self.sb.get_page_source())]
        self.sb.execute_script(
            f"document.forms.groupedSearchForm.page.value={page};"
            "changeGroupedTenderTablePageAjax();")
        deadline = time.monotonic() + WAIT_TIMEOUT
        while True:
            html = self.sb.get_page_source()
            urls = [e["url"] for e in parse_listing(html)]
            if urls and urls != before:
                return html
            if time.monotonic() >= deadline:
                return html
            time.sleep(POLL_SECONDS)

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
    common.append_full_page_text(folder, tender_code, html, PAGE_CONTENT_SELECTOR)
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
        run_notes.note_reason(run_notes.PORTAL)
        return common.TENDER_PARTIAL, tender

    zip_path = None
    try:
        zip_path = session.download_documents(download_url, folder)
        tender["attachments"], any_failed = common.unpack_zip(zip_path, folder)
    except BotBlockedError:
        raise  # a Cloudflare challenge ends the run -- it is not a per-tender failure
    except Exception as e:
        log.warning("       document download FAILED for %s: %s", tender_code, e)
        run_notes.note_reason(run_notes.DOWNLOAD, "document package")
        return common.TENDER_PARTIAL, tender
    finally:
        if zip_path and os.path.exists(zip_path):
            os.remove(zip_path)

    reporting.documents_line(log, len(tender["attachments"]), len(documents))
    return (common.TENDER_PARTIAL if any_failed else common.SITE_SUCCESS), tender


def collect_all_listing_entries(session, limit: int = 0, problems: list | None = None) -> list[dict]:
    """
    Every open tender, page by page. `limit` of 0 means all of them.

    Stops at the pager's page count, at a page with nothing new, or once `limit`
    is reached. Logs an error if fewer tenders turn up than the pager says there
    are, so a gap is never silent.

    Raises StructureChangedError if the first page has no tender rows: the
    portal always has open tenders, so that means the markup changed.

    If a later page cannot be opened, the tenders from the pages before it are
    kept (one broken page must not cost the whole run) and a note is added to
    `problems`, so the caller can report that the listing was not read in full.
    A Cloudflare block or a dead browser is not caught here: it ends the attempt.
    """
    html = session.get(LIST_URL, wait_selector=DETAIL_LINK_SELECTOR)
    entries = parse_listing(html)
    if not entries:
        raise common.StructureChangedError("No tender rows on the open tenders page.")
    pages = total_pages(html)
    counts = listing_counts(html)
    expected = counts[1] if counts else len(entries)

    page = 1
    while not (limit and len(entries) >= limit) and page < pages:
        page += 1
        time.sleep(PAUSE_SECONDS)
        known = {e["url"] for e in entries}
        try:
            page_html = session.open_page(page)
        except BotBlockedError:
            raise
        except Exception as exc:
            if browser_is_gone(exc):
                raise BrowserLostError(str(exc)) from exc
            reason = (str(exc).strip().splitlines() or [""])[0][:200]
            log.error("listing: page %d of %d could not be opened (%s: %s) -- carrying on with "
                      "the %d tender(s) found so far", page, pages, type(exc).__name__, reason,
                      len(entries))
            if problems is not None:
                problems.append(f"page {page} of {pages}")
            break
        new = [e for e in parse_listing(page_html) if e["url"] not in known]
        if not new:
            log.warning("listing: page %d of %d had no new tenders -- stopping", page, pages)
            break
        entries.extend(new)

    if not limit and len(entries) < expected:
        log.error("listing: the pager says %d open tender(s) but only %d were found -- "
                  "some tenders are not being seen", expected, len(entries))
    return entries[:limit] if limit else entries


def order_for_resume(entries: list[dict], known_urls) -> tuple[list[dict], int]:
    """
    Put the tenders we do not hold yet first, then the ones we already have, so
    that a run Cloudflare stops part-way keeps its progress.

    Returns (ordered entries, how many are new).
    """
    known_urls = known_urls or frozenset()
    new = [e for e in entries if e["url"] not in known_urls]
    old = [e for e in entries if e["url"] in known_urls]
    return new + old, len(new)


def _scrape_site(limit: int = 0, output_dir: str = "tenders_data",
                 headless: bool = True, known_urls=None,
                 resume: bool = False, state: dict | None = None) -> tuple[int, list[dict]]:
    """
    Scrape every open SA tender. `limit` of 0 means every tender found.

    `state` is shared between whole-run attempts: {"done": URLs already finished,
    "codes": every tender/site code seen}. A later attempt skips what is done, so
    a browser that died part-way costs only the tenders after it.

    Returns (site_code, tenders). A failed login with credentials set is
    SITE_LOGIN_FAILED, but the page text is still scraped.

    With resume=True a Cloudflare block after at least one tender was scraped
    is not a failure: what was scraped is kept and the next run carries on with
    the tenders not stored yet.
    """
    username, password = credentials()
    # Say plainly whether the secrets reached this process. Values are never logged.
    log.info("login: SA_USERNAME %s, SA_PASSWORD %s",
             "set" if username else "MISSING", "set" if password else "MISSING")
    tenders: list[dict] = []
    tender_codes: set[int] = set()
    done = state["done"] if state is not None else set()
    logged_in = False
    login_didnt_work = False
    try:
        with BrowserSession(headless=headless) as session:
            if username and password:
                logged_in = session.login()
                login_didnt_work = not logged_in
                if logged_in:
                    log.info("login: ok")
                else:
                    log.error("login: FAILED -- continuing without documents")
            else:
                log.warning("login: no credentials -- documents will be skipped and "
                            "tenders with documents will be TENDER_PARTIAL")

            listing_problems: list[str] = []
            entries = collect_all_listing_entries(session, limit, listing_problems)
            log.info("listing: %d tender(s) found", len(entries))
            if resume:
                entries, new_count = order_for_resume(entries, known_urls)
                log.info("resume: %d new tender(s) first, then %d already stored",
                         new_count, len(entries) - new_count)
            run_notes.note_total(len(entries))

            tender_codes = {common.SITE_LOGIN_FAILED} if login_didnt_work else set()
            if listing_problems:
                # The pager stopped working part-way. The tenders already listed
                # are still scraped, but the site is reported as changed.
                log.error("listing: not read in full (%s) -- scraping what was found; "
                          "reporting a structure change", ", ".join(listing_problems))
                tender_codes.add(common.SITE_STRUCTURE_CHANGE)
            all_codes = []
            relogins = 0
            for index, entry in enumerate(entries, start=1):
                if entry["url"] in done:
                    continue  # finished by an earlier attempt
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
                    if browser_is_gone(exc):
                        # Not this tender's fault: every later one would fail the same
                        # way, in a second each. End the attempt; a new browser takes over.
                        raise BrowserLostError(str(exc)) from exc
                    reporting.tender_failed(log, index, len(entries), entry["url"], exc)
                    tender_codes.add(common.TENDER_PARTIAL)
                    all_codes.append(common.TENDER_PARTIAL)
                done.add(entry["url"])
                time.sleep(PAUSE_SECONDS)

            reporting.diagnose(log, all_codes, logged_in=logged_in)

        return common.site_code_from(tender_codes), tenders

    except BotBlockedError:
        if resume and tenders:
            log.warning("Cloudflare stopped the run after %d tender(s) -- kept; the next run "
                        "carries on with the tenders not stored yet", len(tenders))
            return common.site_code_from(tender_codes), tenders
        log.error("Cloudflare challenge did not clear -- stopping the run")
        return common.SITE_BOT_BLOCKED, tenders
    except common.StructureChangedError as exc:
        log.error("structure change: %s", exc)
        return common.SITE_STRUCTURE_CHANGE, tenders
    except Exception as exc:
        log.error("run failed: %s: %s", type(exc).__name__, exc)
        return common.SITE_TOTAL_FAILURE, tenders
    finally:
        if state is not None:
            state["codes"] |= tender_codes


def _scrape_with_retries(limit: int = 0, output_dir: str = "tenders_data",
                         headless: bool = True,
                         attempts: int = BROWSER_ATTEMPTS,
                         known_urls=None, resume: bool = False) -> tuple[int, list[dict]]:
    """
    Run the scrape, starting again with a brand-new browser when it fails as a
    whole (SITE_TOTAL_FAILURE: the browser would not start, crashed, or the site
    could not be read), up to `attempts` times.

    Tenders finished by an attempt are kept and never redone, so a crash part-way
    costs only the tenders after it. A Cloudflare block is retried only while
    nothing has been scraped (with tenders in hand it is returned as before).
    A failed login, structure change or rate limit is never retried.

    If every attempt fails, the result is TOTAL_FAILURE with whatever was
    scraped. If a retry finishes, the result covers the tenders of all attempts.
    """
    state = {"done": set(), "codes": set()}
    all_tenders: list[dict] = []
    code = common.SITE_TOTAL_FAILURE
    for attempt in range(1, attempts + 1):
        log.info("browser attempt %d/%d", attempt, attempts)
        code, tenders = _scrape_site(limit, output_dir, headless, known_urls, resume, state)
        all_tenders.extend(tenders)
        if code == common.SITE_TOTAL_FAILURE:
            retry = True
        elif code == common.SITE_BOT_BLOCKED:
            retry = not all_tenders
        else:
            retry = False
        if not retry:
            break
        if attempt < attempts:
            log.warning("attempt %d/%d got %s after %d tender(s) -- retrying with a new "
                        "browser in %ds", attempt, attempts, reporting.code_name(code),
                        len(all_tenders), 15 * attempt)
            time.sleep(15 * attempt)

    if common.SITE_TOTAL_FAILURE <= code <= common.SITE_RATE_LIMITED:
        return code, all_tenders
    # The last attempt finished. An earlier attempt may have seen something worse
    # (a login failure, a partial tender); keep that in the final code.
    return common.site_code_from(state["codes"] | {code}), all_tenders


@reporting.reported(SOURCE_ID.upper())
def run_scraper(limit: int = 0, output_dir: str = "tenders_data",
                headless: bool = True, known_urls=None,
                resume: bool = False) -> common.ScrapeResult:
    """
    The pipeline's entry point. Returns (error code, site name, tenders scraped)
    and nothing else -- the tenders themselves are left in output_dir.
    """
    code, tenders = _scrape_with_retries(limit, output_dir, headless,
                                          known_urls=known_urls, resume=resume)
    return common.ScrapeResult(code, SOURCE_ID, len(tenders))


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scrape open SA Tenders and Contracts tenders.")
    parser.add_argument("--visible", action="store_true", help="show the browser window")
    args = parser.parse_args()

    reporting.configure_logging()
    run_scraper(headless=not args.visible)  # no cap; for a capped local run use: python manager.py --local


if __name__ == "__main__":
    main()