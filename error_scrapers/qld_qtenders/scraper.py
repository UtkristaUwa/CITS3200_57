"""
error_scrapers/qld_qtenders/scraper.py

Scraper for QTenders (qtenders.hpw.qld.gov.au), the Queensland Government
tenders portal. Same shape as error_scrapers/grant_connect/scraper.py: every
function here is covered by test_cases/test_qld_qtenders_scraper.py.

Per tender, saves into its own folder (via common.py's
tender_dir/save_page_text/download_attachment):
    __tender__<VP reference>.txt -- the search record plus the VendorPanel
                                    preview page's own fields
    <attachment files>           -- raw downloads, where there are any
    <attachment>.txt             -- extracted text per attachment

No browser needed. The search page is a Blazor WebAssembly app, but the app
itself gets its results from a plain JSON endpoint (POST /api/search/tenders),
so we ask that directly. Each tender's detail lives on VendorPanel's public
preview page, which is server-rendered HTML.

Documents:
    The public VendorPanel preview prints only a document *count*. The files
    come from a signed-in VendorPanel supplier account, in a real browser:
    sign in, FOLLOW the tender (nothing downloads until it is followed), then
    download its "Request package" -- one zip holding every attachment plus a
    summary PDF. Credentials: QLD_USERNAME (the account's email address) and
    QLD_PASSWORD. Without them, a tender that advertises documents is reported
    TENDER_PARTIAL (page text saved, attachments missing).

    Following is an account action. The run reports how many of the tenders
    that have documents are confirmed followed, and any shortfall is logged
    as an error rather than hidden.
"""

import contextlib
import html as html_lib
import os
import re
import shutil
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from error_scrapers import common, reporting, run_notes

log = reporting.site_logger("QLD_QTENDERS")

SOURCE_ID = "qld_qtenders"
BASE_URL = "https://qtenders.hpw.qld.gov.au"
SEARCH_API_URL = f"{BASE_URL}/api/search/tenders"
VENDORPANEL_BASE = "https://www.vendorpanel.com.au"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
}

# tenderStatusIds "1" is Open -- the same filter the site's own "Open"
# search uses. The API caps pageSize at 25 whatever is asked for.
PAGE_SIZE = 25
OPEN_STATUS = "1"

# The label cells on a VendorPanel preview page. If none are found on an
# otherwise real page, the page's structure has changed -- the
# structure-changed fixture renames exactly this class.
FIELD_SELECTOR = ".opportunityPreviewMinHeading"
SECTION_CLASS = "opportunityPreviewMaxHeading"
LABEL_CLASS = "opportunityPreviewMinHeading"
VALUE_CLASS = "opportunityPreviewContent"

# Attachment links on a VendorPanel page, for the (signed-in supplier) case
# where they are rendered at all. Selected on the route, not the link text.
DOCUMENT_LINK_SELECTOR = (
    "a[href*='DownloadTenderDocument'], a[href*='DownloadDocument'], "
    "a[href*='GetTenderDocument']"
)

PAUSE_SECONDS = 0.5

# ---------------------------------------------------------------------------
# VendorPanel (signed-in documents)
# ---------------------------------------------------------------------------

# The "Public Tenders" list: one table row per tender, its id the VendorPanel
# number (<tr id="528160"> is VP528160). Signing in is requested by loading it.
VP_LIST_URL = f"{VENDORPANEL_BASE}/Members/?do=Tenders:AllTenders"
VP_PACKAGE_URL = f"{VENDORPANEL_BASE}/Members/VendorDownloadOpportunityPackage.aspx?opportunityId={{number}}"

# Sign-in is two steps on one form: the email and Next, then the password.
LOGIN_FORM = "form#loginForm"
LOGIN_USER = f"{LOGIN_FORM} input#UserName"
LOGIN_PASSWORD = f"{LOGIN_FORM} input[type='password']"
LOGIN_SUBMIT = f"{LOGIN_FORM} input[type='submit']"

# Every signed-in page has a Logout link in the user menu.
SIGNED_IN_SELECTOR = "a[href='/logout.axd']"

SEARCH_BOX = "input#filterText"
LIST_TABLE = "table#tendersDataTable"
ROW_SELECTOR = "tr[id='{number}']"

# The Follow toggle. Selected on its own route, never on position: the same
# row also carries a Hide link (/hpt.axd), which must never be clicked. An
# already-followed row's toggle goes to /ufpt.axd, which this does not match.
FOLLOW_LINK = "a[href*='/fpt.axd']"

# The Download button on the package page. (The sign-in page's Next button
# is also #btnGo, so this is selected on its full name.)
PACKAGE_BUTTON = "input[name='PopUpMaster$masterMain$btnGo']"

WAIT_TIMEOUT = 30            # seconds for a page's table or form to appear
# Seconds for the search box to narrow the list to one row. 10 was too short: on the
# 5am run 67 of 100 tenders were reported "not on the Public Tenders list" when the
# list had simply not narrowed yet, and every one the retry pass reached (25 s) was
# found. A row that appears sooner returns at once, so a longer limit costs nothing
# unless the search really is slow.
FILTER_WAIT_SECONDS = int(os.environ.get("QLD_FILTER_WAIT_SECONDS", "30"))
RELOAD_SECONDS = 3           # pause after clicking Follow, which reloads the page
FOLLOW_CHECKS = 6            # times to look for "Unfollow" after clicking Follow
FOLLOW_CHECK_PAUSE = 5       # seconds between those looks
# A package can take minutes to build, and the Download click does not return
# until the portal starts to answer. Chrome's own driver gives up on a click
# after 120s, so a slow package used to fail even though it would have arrived.
# The file has to start arriving within DOWNLOAD_WAIT_SECONDS of the click
# returning; once it has, it gets until it stalls or the hard maximum.
DOWNLOAD_WAIT_SECONDS = int(os.environ.get("QLD_DOWNLOAD_WAIT_SECONDS", "240"))
DOWNLOAD_STALL_SECONDS = int(os.environ.get("QLD_DOWNLOAD_STALL_SECONDS", "300"))
DOWNLOAD_HARD_MAX_SECONDS = int(os.environ.get("QLD_DOWNLOAD_HARD_MAX_SECONDS", "900"))
FIND_ATTEMPTS = int(os.environ.get("QLD_FIND_ATTEMPTS", "3"))  # searches before a tender is called absent
# Three tries, not two: on the 5am run three packages gave nothing for 240 s on the
# first click and arrived about a minute after the second, and one failed both tries.
DOWNLOAD_ATTEMPTS = int(os.environ.get("QLD_DOWNLOAD_ATTEMPTS", "3"))
DOWNLOAD_POLL_SECONDS = 1.0

# After the main loop, tenders whose documents could not be reached (typically
# the search never narrowed to their row) get another go with a fresh list and
# a more patient search, up to RETRY_ROUNDS times each. The pass stops after
# RETRY_PASS_MAX_SECONDS so it can never stretch the nightly run without bound.
# The old limit of one hour reached 11 of 75 tenders; 6 hours covers every tender
# at the pace seen (1-8 minutes each) and still leaves the 18 hour job timeout
# well clear of the rest of the run.
RETRY_PASS_MAX_SECONDS = int(os.environ.get("QLD_RETRY_PASS_MAX_SECONDS", "21600"))
RETRY_FILTER_WAIT_SECONDS = int(os.environ.get("QLD_RETRY_FILTER_WAIT_SECONDS", "45"))
RETRY_ROUNDS = int(os.environ.get("QLD_RETRY_ROUNDS", "3"))
RETRY_PAUSE_SECONDS = 3

# A session that lapses mid-run is replaced by signing in again, this many
# times at most.
MAX_RELOGINS = 3


def blocked_code(exc: httpx.HTTPStatusError) -> int | None:
    """
    429 -> SITE_RATE_LIMITED, 403 -> SITE_BOT_BLOCKED, anything else None.
    Either one ends the run: we back off rather than retry or work around it.
    """
    status = exc.response.status_code
    if status == 429:
        return common.SITE_RATE_LIMITED
    if status == 403:
        return common.SITE_BOT_BLOCKED
    return None


# ---------------------------------------------------------------------------
# Search API
# ---------------------------------------------------------------------------

def search_body(page: int) -> dict:
    """The request body the site's own search page sends for one page of open tenders."""
    return {
        "keywords": "",
        "agencyIds": [],
        "categoryIds": [],
        "locationIds": [],
        "productServiceIds": [],
        "tenderStatusIds": [OPEN_STATUS],
        "pageNumber": page,
        "pageSize": PAGE_SIZE,
        "sortBy": "Opens",
    }


def parse_search_page(data) -> tuple[list[dict], int | None]:
    """
    Pull the tenders and the page count out of one search response.

    Raises StructureChangedError if the response isn't shaped the way the
    site's own app expects -- that means the API changed under us.
    """
    if not isinstance(data, dict) or not isinstance(data.get("tenders"), list):
        raise common.StructureChangedError("QTenders search response has no 'tenders' list.")
    tenders = [t for t in data["tenders"] if t.get("vpReference") and t.get("tenderPreviewUrl")]
    return tenders, data.get("totalPages")


def collect_all_tenders(client, limit: int = 0, problems: list | None = None) -> list[dict]:
    """
    Walk every page of open tenders, stopping at the reported page count, at
    an empty page, or once `limit` is reached. `limit` of 0 means every
    open tender.
    """
    tenders, seen, page = [], set(), 1
    while True:
        try:
            response = common.request_with_retries(
                client, "post", SEARCH_API_URL, json=search_body(page), headers=HEADERS, timeout=30.0)
            response.raise_for_status()
        except Exception as exc:
            # A later page that keeps failing must not cost the pages before it.
            if common.listing_page_failed(log, page, exc, len(tenders), problems):
                break
            raise
        try:
            data = response.json()
        except ValueError:
            raise common.StructureChangedError("QTenders search did not return JSON.") from None
        page_tenders, total_pages = parse_search_page(data)

        new = [t for t in page_tenders if t["vpReference"] not in seen]
        if not new:
            break
        for tender in new:
            seen.add(tender["vpReference"])
            tenders.append(tender)
        if limit and len(tenders) >= limit:
            return tenders[:limit]
        if total_pages is not None and page >= total_pages:
            break
        page += 1
        time.sleep(PAUSE_SECONDS)
    return tenders


# ---------------------------------------------------------------------------
# VendorPanel preview page
# ---------------------------------------------------------------------------

def parse_detail(html: str) -> tuple[dict, int]:
    """
    Pull the fields off a VendorPanel public tender preview page.

    The page is a flat run of section headings, each followed by
    label/value rows. Labels are kept under their section because some
    ("Business Name") appear in more than one.

    Returns (fields, status_code) where fields is
    {"sections": [(section, label, value), ...], "documents_advertised": int,
     "documents": [{file_name, url}, ...]}.
    """
    soup = BeautifulSoup(html, "html.parser")
    if not soup.select(FIELD_SELECTOR):
        return {}, common.SITE_STRUCTURE_CHANGE

    sections = []
    section = "Tender Details"
    for element in soup.find_all(class_=[SECTION_CLASS, LABEL_CLASS]):
        if SECTION_CLASS in element.get("class", []):
            section = element.get_text(" ", strip=True)
            continue
        value_el = element.find_next_sibling(class_=VALUE_CLASS)
        if value_el is None:
            continue
        label = element.get_text(" ", strip=True).rstrip(":")
        sections.append((section, label, value_el.get_text("\n", strip=True)))

    # Tenders with no attachments print "None..." rather than 0.
    count = next((v for _, label, v in sections if label == "Documents"), "")
    match = re.search(r"\d+", count)

    return {
        "sections": sections,
        "documents_advertised": int(match.group()) if match else 0,
        "documents": parse_documents(soup),
    }, common.SITE_SUCCESS


def parse_documents(soup) -> list[dict]:
    """The downloadable attachments rendered on the page -- none for the public preview."""
    documents = []
    for anchor in soup.select(DOCUMENT_LINK_SELECTOR):
        href = anchor.get("href")
        if not href:
            continue
        name = anchor.get("title") or anchor.get_text(" ", strip=True)
        documents.append({
            "file_name": common.sanitise_filename(name or "document.bin"),
            "url": urljoin(VENDORPANEL_BASE, href),
        })
    return documents


def html_to_text(value: str | None) -> str:
    """The search API's `details` field is HTML; the page text wants plain text."""
    if not value:
        return ""
    return BeautifulSoup(html_lib.unescape(value), "html.parser").get_text("\n", strip=True)


def agency_name(tender: dict) -> str | None:
    """
    The buying agency. The search record's department is sometimes the
    placeholder "Unspecified" even though the tender names its business
    (VP527937: department "Unspecified", business Queensland Health), so a
    placeholder falls through to the business name.
    """
    for key in ("departmentName", "businessName", "issuerName"):
        value = (tender.get(key) or "").strip()
        if value and value.lower() != "unspecified":
            return value
    return None


def format_detail_text(tender: dict, detail: dict) -> str:
    """
    Render the tender as its page-text file: the search record first (it is
    the cleanest source of the headline fields), then every section of the
    VendorPanel preview.
    """
    lines = [
        "=" * 80,
        f"QTENDERS DETAILS: {tender.get('title') or tender.get('vpReference')}",
        "=" * 80,
        "",
    ]
    summary = [
        ("VP Reference", tender.get("vpReference")),
        ("Buyers Reference", tender.get("buyersReference")),
        ("Agency", agency_name(tender)),
        ("Status", tender.get("tenderStatus")),
        # The API's timestamps are UTC with no offset; the preview sections
        # below print the same dates in Queensland time.
        ("Opens (UTC)", tender.get("opens")),
        ("Closes (UTC)", tender.get("closes")),
        ("Categories", ", ".join(tender.get("categories") or [])),
        ("Locations", ", ".join(tender.get("locations") or [])),
        ("Products / Services", ", ".join(tender.get("productsServices") or [])),
    ]
    lines += [f"{label}: {value}" for label, value in summary if value]

    details = html_to_text(tender.get("details"))
    if details:
        lines += ["", "DETAILS:", "-" * 40, details]

    current = None
    for section, label, value in (detail or {}).get("sections", []):
        if section != current:
            current = section
            lines += ["", section.upper() + ":", "-" * 40]
        if "\n" in value:
            lines.append(f"{label}:")
            lines += [f"    {line}" for line in value.splitlines()]
        else:
            lines.append(f"{label}: {value}")

    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# VendorPanel: credentials, page parsing, browser session
# ---------------------------------------------------------------------------

def credentials():
    """Return (username, password) from the environment, or (None, None).
    The username is the VendorPanel account's email address."""
    return (
        os.environ.get("QLD_USERNAME") or None,
        os.environ.get("QLD_PASSWORD") or None,
    )


class SessionLapsedError(Exception):
    """VendorPanel served its sign-in page instead of what was asked for."""


def vp_number(reference: str | None) -> str | None:
    """"VP528160" -> "528160", the id VendorPanel's list rows and package page use."""
    match = re.fullmatch(r"VP(\d+)", (reference or "").strip(), re.I)
    return match.group(1) if match else None


def is_signed_in(html: str) -> bool:
    """True if the page was served to a signed-in VendorPanel user."""
    return BeautifulSoup(html, "html.parser").select_one(SIGNED_IN_SELECTOR) is not None


def parse_row(html: str, number: str) -> dict | None:
    """
    One tender's row on the Public Tenders list, or None if it is not on the page.

    {"followed": bool, "can_follow": bool, "can_download": bool, "documents": int | None}
    A followed row shows "Unfollow", and its icons become live: the documents
    icon says how many documents the request has, the arrow downloads the package.
    """
    soup = BeautifulSoup(html, "html.parser")
    row = soup.find("tr", id=str(number))
    if row is None:
        return None
    count = re.search(r"Access (\d+) documents? attached", str(row))
    return {
        "followed": row.select_one(".followedTender") is not None,
        "can_follow": row.select_one(FOLLOW_LINK) is not None,
        "can_download": row.select_one("a[onclick*='VendorDownloadOpportunityPackage']") is not None,
        "documents": int(count.group(1)) if count else None,
    }


def is_timeout_error(exc: BaseException) -> bool:
    """True for a timeout from the browser driver (ReadTimeoutError, TimeoutException, ...)."""
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


class VendorPanelSession:
    """
    One SeleniumBase Chrome signed in to VendorPanel for the whole run. Use as
    a context manager so the browser always closes. (VendorPanel is not behind
    Cloudflare, so this is a plain browser, not UC mode.)
    """

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._sb_cm = None
        self.sb = None
        self._on_list = False
        # How long find_row waits for the search to narrow to the tender's row.
        # The retry pass raises it temporarily.
        self.filter_wait = FILTER_WAIT_SECONDS

    def __enter__(self):
        from seleniumbase import SB

        options = {"headless": self._headless}
        if os.environ.get("RUNNING_IN_CONTAINER", "").lower() in ("1", "true", "yes"):
            options["chromium_arg"] = "disable-dev-shm-usage,disable-gpu"
        self._sb_cm = SB(**options)
        self.sb = self._sb_cm.__enter__()
        try:
            log.info("chrome %s", self.sb.driver.capabilities.get("browserVersion"))
        except Exception:
            pass  # only for the logs; never worth failing the run
        # SeleniumBase saves clicked downloads into ./downloaded_files.
        self._downloads_dir = os.path.join(os.getcwd(), "downloaded_files")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._sb_cm is not None:
            self._sb_cm.__exit__(exc_type, exc_val, exc_tb)

    def _open(self, url: str) -> None:
        """Open a page, loading it again if the load itself fails (timeout, connection reset)."""
        common.load_page_with_retries(lambda: self.sb.open(url), url)

    def _debug_dump(self, label: str) -> None:
        """Log what the browser is looking at, flattened to one line for Cloud Logging."""
        try:
            log.warning("debug %s url: %s", label, self.sb.get_current_url())
            html = self.sb.get_page_source()[:1500].replace("\n", " ").replace("\r", " ")
            log.warning("debug %s html: %s", label, html)
        except Exception as e:
            log.warning("debug %s could not read page: %s", label, e)

    def login(self) -> bool:
        """
        Sign in: email and Next, then the password. True once the page shows
        the Logout link; False if nothing signs us in within fifteen seconds.
        """
        username, password = credentials()
        self._on_list = False
        self._open(VP_LIST_URL)
        self.sb.wait_for_element(LOGIN_USER, timeout=WAIT_TIMEOUT)
        self.sb.type(LOGIN_USER, username)
        self.sb.click(LOGIN_SUBMIT)
        self.sb.wait_for_element(LOGIN_PASSWORD, timeout=WAIT_TIMEOUT)
        self.sb.type(LOGIN_PASSWORD, password)
        self.sb.click(LOGIN_SUBMIT)
        for _ in range(15):
            time.sleep(1)
            if self.sb.is_element_present(SIGNED_IN_SELECTOR):
                return True
        self._debug_dump("login")
        return False

    def _open_list(self) -> None:
        self._open(VP_LIST_URL)
        try:
            self.sb.wait_for_element(LIST_TABLE, timeout=WAIT_TIMEOUT)
        except Exception:
            pass
        if not is_signed_in(self.sb.get_page_source()):
            raise SessionLapsedError("VendorPanel is showing its sign-in page")
        self._on_list = True

    def find_row(self, number: str) -> dict | None:
        """Search the Public Tenders list for VP<number> and read its row (None if absent)."""
        for attempt in range(1, FIND_ATTEMPTS + 1):
            if not self._on_list:
                self._open_list()
            self.sb.type(SEARCH_BOX, f"VP{number}")
            try:
                self.sb.wait_for_element(ROW_SELECTOR.format(number=number), timeout=self.filter_wait)
            except Exception:
                # The filter sometimes does not narrow in time; reload and look once more.
                self._on_list = False
                continue
            return parse_row(self.sb.get_page_source(), number)
        return None

    def follow(self, number: str) -> bool:
        """
        Click the row's Follow toggle, then look for the row to say Unfollow.

        The portal reloads after the click and can be slow, so it is checked a
        few times rather than once. If the row still offers Follow after the
        second look, Follow is clicked once more. That is safe: an already-
        followed row has an Unfollow link instead, which FOLLOW_LINK never matches.
        """
        follow_click = f"{ROW_SELECTOR.format(number=number)} {FOLLOW_LINK}"
        self.sb.click(follow_click)
        clicked_again = False
        for check in range(FOLLOW_CHECKS):
            time.sleep(RELOAD_SECONDS if check == 0 else FOLLOW_CHECK_PAUSE)
            self._on_list = False
            row = self.find_row(number)
            if row and row["followed"]:
                return True
            if row and row["can_follow"] and not clicked_again and check >= 1:
                clicked_again = True
                log.info("       VP%s still shows Follow; clicking it once more", number)
                try:
                    self.sb.click(follow_click)
                except Exception as e:
                    log.warning("       second Follow click on VP%s failed: %s", number, e)
        return False

    def download_package(self, number: str, folder: str) -> str:
        """
        Open the tender's package page, click Download, and move the zip that
        lands in SeleniumBase's downloads folder into `folder`. Returns its path.

        If the click does not return in time (the driver's 120s limit), the
        download may still be happening, so the folder is watched anyway. If no
        file arrives, the page is opened and clicked once more (DOWNLOAD_ATTEMPTS
        tries in all) before giving up.
        """
        os.makedirs(self._downloads_dir, exist_ok=True)
        # Taken once, so a file from an earlier try that arrives late still counts.
        before = set(os.listdir(self._downloads_dir))
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            self._on_list = False
            self._open(VP_PACKAGE_URL.format(number=number))
            try:
                self.sb.wait_for_element(PACKAGE_BUTTON, timeout=WAIT_TIMEOUT)
            except Exception:
                if not is_signed_in(self.sb.get_page_source()) and self.sb.is_element_present(LOGIN_USER):
                    raise SessionLapsedError("VendorPanel is showing its sign-in page")
                raise common.StructureChangedError(f"no Download button ({PACKAGE_BUTTON}) on the package page")
            try:
                self.sb.click(PACKAGE_BUTTON)
            except Exception as e:
                if not is_timeout_error(e):
                    raise
                log.warning("       documents: the Download click for VP%s did not return (%s); "
                            "watching for the file anyway", number, type(e).__name__)
            name = common.wait_for_download(
                self._downloads_dir, before, DOWNLOAD_WAIT_SECONDS,
                poll=DOWNLOAD_POLL_SECONDS, stall_seconds=DOWNLOAD_STALL_SECONDS,
                hard_max=DOWNLOAD_HARD_MAX_SECONDS)
            if name:
                target = os.path.join(folder, name)
                shutil.move(os.path.join(self._downloads_dir, name), target)
                return target
            log.warning("       documents: no package for VP%s after %ds (try %d of %d)",
                        number, DOWNLOAD_WAIT_SECONDS, attempt, DOWNLOAD_ATTEMPTS)
        raise TimeoutError(f"the package download for VP{number} did not finish")


def fetch_documents(vendorpanel, result: dict, advertised: int) -> int:
    """
    Follow the tender if it is not followed yet, then download its package into
    its folder. Updates `result` in place (attachments, followed,
    documents_gated, session_lapsed) and returns the tender's status code.
    """
    reference = result["tender_id"]
    number = vp_number(reference)
    if number is None:
        log.warning("       documents: %s is not a VendorPanel number -- cannot fetch them", reference)
        return common.TENDER_PARTIAL

    try:
        row = vendorpanel.find_row(number)
        if row is None:
            log.warning("       documents: VP%s is not on VendorPanel's Public Tenders list", number)
            return common.TENDER_PARTIAL
        if not row["followed"]:
            if not row["can_follow"]:
                log.warning("       documents: VP%s has no Follow toggle", number)
                result["cannot_follow"] = True
                run_notes.note_reason(run_notes.PORTAL, f"VP{number} cannot be followed")
                return common.TENDER_PARTIAL
            log.info("       following VP%s", number)
            if not vendorpanel.follow(number):
                log.warning("       documents: followed VP%s but it still does not show as followed", number)
                return common.TENDER_PARTIAL
        result["followed"] = True

        zip_path = None
        try:
            zip_path = vendorpanel.download_package(number, result["folder"])
            attachments, any_failed = common.unpack_zip(zip_path, result["folder"])
        finally:
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
    except SessionLapsedError as e:
        log.warning("       documents: %s", e)
        result["session_lapsed"] = True
        return common.TENDER_PARTIAL
    except Exception as e:
        if common.browser_is_gone(e):
            # Not this tender's fault: every tender after it would fail the same way.
            raise common.BrowserLostError(f"{type(e).__name__}: {e}") from e
        log.warning("       documents FAILED for VP%s: %s: %s", number, type(e).__name__, e)
        result["package_failed"] = True  # the retry pass tries the download again
        return common.TENDER_PARTIAL

    result["package_failed"] = False
    result["attachments"] = attachments
    # The package also holds a summary PDF, so it can hold more files than were advertised.
    if len(attachments) < advertised:
        log.warning("       documents: %d advertised, only %d in the package", advertised, len(attachments))
        result["documents_gated"] = True
        return common.TENDER_PARTIAL
    result["documents_gated"] = False
    reporting.documents_line(log, len(attachments), advertised)
    return common.TENDER_PARTIAL if any_failed else common.SITE_SUCCESS


def retry_unfollowed(vendorpanel, results: list[dict], relogin=None,
                     max_seconds: float = None, sleep=time.sleep,
                     clock=time.monotonic, restart=None) -> dict[str, int]:
    """
    Second pass, run after every tender has had its first go: try again for each
    tender that has documents but is still not followed (or was followed but whose
    package download failed), up to RETRY_ROUNDS times each. The usual cause is
    the search not narrowing to the tender's row in time, which a fresh list and
    a more patient search often gets past (as doing it by hand does).

    `relogin` is called if the session lapses; it returns True when signed in
    again. `restart` is called if the browser itself dies; it returns a fresh,
    signed-in session (or None when it cannot), and the pass carries on with it.
    Returns {tender_id: status code} for every tender it tried, so the
    caller can replace the codes from the first pass.
    """
    if max_seconds is None:
        max_seconds = RETRY_PASS_MAX_SECONDS
    # "Gated" means the tender's public page advertises more documents than it
    # links, so the rest can only come through VendorPanel and the tender has to
    # be followed. A tender whose documents are all linked never needs following.
    def still_to_do():
        return [r for r in results
                if r.get("documents_advertised", 0) > 0 and r.get("documents_gated")
                and (not r["followed"] or r.get("package_failed"))
                and not r.get("cannot_follow")]  # no Follow toggle: another round cannot help

    todo = still_to_do()
    if not todo:
        return {}

    log.info("retry pass: %d tender(s) with VendorPanel-only documents are not followed yet -- trying them again",
             len(todo))
    codes: dict[str, int] = {}
    recovered = 0
    started = clock()
    previous_wait = vendorpanel.filter_wait
    vendorpanel.filter_wait = RETRY_FILTER_WAIT_SECONDS
    stop = False

    def fetch(result, advertised):
        """fetch_documents, on a fresh browser if this one dies. None if there is no browser left."""
        nonlocal vendorpanel
        for _ in range(1 + MAX_BROWSER_RESTARTS):  # restart_browser caps the restarts itself
            try:
                return fetch_documents(vendorpanel, result, advertised)
            except common.BrowserLostError as exc:
                log.warning("retry pass: the browser is gone (%s)", exc)
                fresh = restart() if restart is not None else None
                if fresh is None:
                    return None
                vendorpanel = fresh
                vendorpanel.filter_wait = RETRY_FILTER_WAIT_SECONDS
        return None

    try:
        for round_number in range(1, RETRY_ROUNDS + 1):
            pending = still_to_do()
            if not pending or stop:
                break
            if round_number > 1:
                log.info("retry pass: round %d of %d -- %d tender(s) still not followed",
                         round_number, RETRY_ROUNDS, len(pending))
            for result in pending:
                if clock() - started > max_seconds:
                    log.warning("retry pass: out of time (%ds) -- leaving the rest as they are", max_seconds)
                    stop = True
                    break
                reference = result["tender_id"]
                advertised = result["documents_advertised"]
                vendorpanel._on_list = False  # open the list afresh, not the page the last search left
                sleep(RETRY_PAUSE_SECONDS)
                result["session_lapsed"] = False
                code = fetch(result, advertised)
                if code is None:
                    log.error("retry pass: the browser is gone and could not be restarted -- stopping")
                    stop = True
                    break
                if result["session_lapsed"] and relogin is not None and relogin():
                    result["session_lapsed"] = False
                    code = fetch(result, advertised)
                    if code is None:
                        log.error("retry pass: the browser is gone and could not be restarted -- stopping")
                        stop = True
                        break
                if result["session_lapsed"]:
                    log.warning("retry pass: the VendorPanel session lapsed and could not be "
                                "restored -- stopping")
                    stop = True
                    break
                if result["followed"] and not result.get("package_failed"):
                    recovered += 1
                    if result.get("public_download_failed"):
                        code = common.TENDER_PARTIAL  # a directly linked document failed earlier
                    log.info("retry pass: %s followed this time", reference)
                else:
                    log.warning("retry pass: %s still could not be fetched (round %d of %d)",
                                reference, round_number, RETRY_ROUNDS)
                    run_notes.note_reason(run_notes.PORTAL)
                codes[reference] = code
                run_notes.note_tender(reference, code)  # replaces the first pass's verdict
    finally:
        vendorpanel.filter_wait = previous_wait
    log.info("retry pass: %d of %d tender(s) followed this time", recovered, len(todo))
    return codes


# ---------------------------------------------------------------------------
# Per-tender and full-run orchestration
# ---------------------------------------------------------------------------

def scrape_opportunity(client, tender: dict, output_dir: str = "tenders_data",
                       vendorpanel=None) -> tuple[int, dict]:
    """
    Scrape one tender (a search-API record) into its own folder. With a signed-in
    `vendorpanel` session, a tender whose documents are not linked on its public
    preview has them fetched through VendorPanel (see fetch_documents).

    Returns (status_code, result). If the preview page can't be fetched, the
    search record alone is still saved as the page text and the tender is
    TENDER_PARTIAL; if it can be fetched but not parsed, nothing is saved
    and the code is SITE_STRUCTURE_CHANGE.
    """
    reference = tender["vpReference"]
    url = tender["tenderPreviewUrl"]

    detail, code, fetch_failed, page_html = {}, common.SITE_SUCCESS, False, ""
    try:
        response = common.request_with_retries(client, "get", url, headers=HEADERS, timeout=30.0)
        response.raise_for_status()
        page_html = response.text
        detail, code = parse_detail(response.text)
    except httpx.HTTPError as e:
        if isinstance(e, httpx.HTTPStatusError) and blocked_code(e) is not None:
            raise  # 429/403: stop the whole run rather than carry on
        log.warning("       preview FAILED: %s: %s", url, e)
        fetch_failed = True
    if code != common.SITE_SUCCESS:
        return code, {}

    folder = common.tender_dir(reference, output_dir)
    common.save_page_text(folder, reference, format_detail_text(tender, detail))
    common.append_full_page_text(folder, reference, page_html)
    common.add_source_url(folder, reference, url)

    result = {
        "tender_id": reference,
        "title": tender.get("title"),
        "folder": folder,
        "attachments": [],
        "source_url": url,
        "documents_gated": False,
        "documents_advertised": 0,
        "followed": False,
        "session_lapsed": False,
    }
    if fetch_failed:
        run_notes.note_reason(run_notes.PAGE, "preview page")  # else "reason not recorded"
        return common.TENDER_PARTIAL, result

    any_failed = False
    for document in detail["documents"]:
        try:
            attachment, extracted = common.download_attachment(
                client, document["url"], folder, document["file_name"], headers=HEADERS
            )
        except Exception as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                raise  # the site says slow down: stop the run, don't keep downloading
            log.warning("       download FAILED: %s: %s", document["url"], e)
            run_notes.note_reason(run_notes.DOWNLOAD, document["file_name"])
            any_failed = True
            continue
        result["attachments"].append(attachment)
        any_failed = any_failed or not extracted

    advertised = detail["documents_advertised"]
    result["documents_advertised"] = advertised
    # Remembered so the retry pass does not turn this tender into a clean
    # success when one of its directly linked documents failed.
    result["public_download_failed"] = any_failed
    result["documents_gated"] = advertised > len(detail["documents"])
    if result["documents_gated"] and vendorpanel is not None:
        code = fetch_documents(vendorpanel, result, advertised)
        if code != common.SITE_SUCCESS:
            run_notes.note_reason(run_notes.PORTAL)
        return (common.TENDER_PARTIAL if any_failed else code), result
    if result["documents_gated"]:
        log.warning("       documents: %d advertised, %d linked -- the rest need a "
                    "VendorPanel supplier login", advertised, len(detail["documents"]))
        run_notes.note_reason(run_notes.PORTAL)
    else:
        reporting.documents_line(log, len(result["attachments"]), advertised)
    if any_failed or result["documents_gated"]:
        return common.TENDER_PARTIAL, result
    return common.SITE_SUCCESS, result


# Chrome sometimes fails to start inside the container. That is tried again with a
# fresh browser; a sign-in that does not take is NOT repeated (a lockout risk).
BROWSER_START_ATTEMPTS = int(os.environ.get("QLD_BROWSER_START_ATTEMPTS", "4"))
BROWSER_START_WAIT_SECONDS = 15
# A browser that dies mid-run is replaced (and signed in again) up to this many times.
MAX_BROWSER_RESTARTS = int(os.environ.get("QLD_BROWSER_RESTARTS", "3"))
# A browser that would not start at the beginning gets another go after the pages
# have been read, when whatever stopped it (memory, a busy container) has often passed.
LATE_START_ATTEMPTS = int(os.environ.get("QLD_LATE_START_ATTEMPTS", "3"))
LATE_START_WAIT_SECONDS = 60


def _close_quietly(stack) -> None:
    """Close a browser's ExitStack; a browser that is already dead may complain, which is fine."""
    try:
        stack.close()
    except Exception as exc:
        log.warning("browser: closing it raised %s: %s (ignored)", type(exc).__name__, exc)


def open_vendorpanel(stack, headless: bool = True,
                     attempts: int = BROWSER_START_ATTEMPTS) -> "VendorPanelSession":
    """
    Start the VendorPanel browser inside `stack`, trying again (with a fresh
    Chrome, waiting longer each time) if it will not start. Raises the last
    error when every attempt fails.
    """
    for attempt in range(1, attempts + 1):
        try:
            return stack.enter_context(VendorPanelSession(headless=headless))
        except Exception as exc:
            if attempt == attempts:
                raise
            wait = BROWSER_START_WAIT_SECONDS * attempt
            log.warning("browser did not start (%s: %s) -- trying again in %ds (attempt %d/%d)",
                        type(exc).__name__, exc, wait, attempt + 1, attempts)
            time.sleep(wait)


def _scrape_site(limit: int = 0, output_dir: str = "tenders_data",
                 headless: bool = True) -> tuple[int, list[dict]]:
    """
    Scrape every open QTenders tender. `limit` of 0 means every tender found.

    Returns (site_code, tenders) -- one entry per tender scraped, each
    listing the attachment files saved for it. With QLD_USERNAME/QLD_PASSWORD
    the documents are fetched through a signed-in VendorPanel browser; a login
    that does not take is SITE_LOGIN_FAILED (page text is still scraped), and no
    credentials at all gives TENDER_PARTIAL for tenders with documents. A 429
    or 403 stops the run at once.
    """
    username, password = credentials()
    have_credentials = bool(username and password)
    # Say plainly whether the secrets reached this process. Values are never logged.
    log.info("login: QLD_USERNAME %s, QLD_PASSWORD %s",
             "set" if username else "MISSING", "set" if password else "MISSING")

    results: list[dict] = []
    try:
        with httpx.Client(follow_redirects=True) as client, contextlib.ExitStack() as stack:
            vendorpanel = None
            logged_in = False
            login_failed = False
            start_status = "none"   # "ok", "login_rejected", "no_browser" or "none" (no credentials)
            restarts = 0
            browser = {"stack": contextlib.ExitStack()}
            stack.callback(lambda: _close_quietly(browser["stack"]))

            def start_browser() -> str:
                """
                Open a fresh Chrome and sign in once. "ok", "login_rejected" (the site
                did not accept the sign-in; never tried again) or "no_browser".
                """
                nonlocal vendorpanel, logged_in
                _close_quietly(browser["stack"])
                browser["stack"] = contextlib.ExitStack()
                vendorpanel, logged_in = None, False
                try:
                    candidate = open_vendorpanel(browser["stack"], headless)
                except Exception as exc:
                    log.error("browser: could not start (%s: %s)", type(exc).__name__, exc)
                    return "no_browser"
                try:
                    signed_in = candidate.login()
                except Exception as exc:
                    if common.browser_is_gone(exc):
                        log.error("browser: died while signing in (%s: %s)", type(exc).__name__, exc)
                        return "no_browser"
                    log.error("login: could not sign in to VendorPanel: %s: %s", type(exc).__name__, exc)
                    signed_in = False
                if not signed_in:
                    return "login_rejected"
                vendorpanel, logged_in = candidate, True
                return "ok"

            def restart_browser(reason) -> bool:
                """Replace a dead browser (signing in again), up to MAX_BROWSER_RESTARTS times."""
                nonlocal restarts, vendorpanel, logged_in
                if restarts >= MAX_BROWSER_RESTARTS:
                    log.error("browser: lost again (%s) and already restarted %d time(s) -- "
                              "carrying on without documents", reason, restarts)
                    vendorpanel, logged_in = None, False
                    return False
                restarts += 1
                log.warning("browser: lost (%s) -- starting a fresh one and signing in (%d/%d)",
                            reason, restarts, MAX_BROWSER_RESTARTS)
                status = start_browser()
                if status != "ok":
                    log.error("browser: could not be replaced (%s) -- carrying on without documents", status)
                return status == "ok"

            def restart_for_retry_pass():
                return vendorpanel if restart_browser("retry pass") else None

            def sign_in_again() -> bool:
                """
                vendorpanel.login() after a lapsed session. login() raises when the
                sign-in form does not appear (a maintenance page, a slow render, a
                browser that has died); start_browser guards the first sign-in
                against that, and this guards the later ones, so a failed re-login
                is a failed re-login and not a crash of the whole run.
                """
                nonlocal logged_in
                try:
                    logged_in = vendorpanel.login()
                except Exception as exc:
                    logged_in = False
                    log.error("login: could not sign in to VendorPanel again (%s: %s)",
                              type(exc).__name__, exc)
                return logged_in

            if have_credentials:
                start_status = start_browser()
                login_failed = start_status != "ok"
                if start_status == "ok":
                    log.info("login: ok")
                else:
                    log.error("login: FAILED -- check QLD_USERNAME (the account's email "
                              "address) and QLD_PASSWORD; carrying on without documents"
                              if start_status == "login_rejected" else
                              "browser: would not start; will try again after the tender pages are read")
            else:
                log.warning("login: no credentials -- documents will be skipped and tenders "
                            "with documents will be TENDER_PARTIAL")

            listing_problems: list[str] = []
            tenders = collect_all_tenders(client, limit, listing_problems)
            log.info("listing: %d tender(s) found", len(tenders))

            tender_codes = {common.SITE_LOGIN_FAILED} if login_failed else set()
            if common.note_listing_problems(log, listing_problems):
                tender_codes.add(common.TENDER_PARTIAL)
            all_codes = []
            code_index = {}  # tender reference -> its position in all_codes
            relogins = 0
            for index, tender in enumerate(tenders, start=1):
                reference = tender.get("vpReference")
                try:
                    # Chrome may die under this tender. A fresh one (signed in again) takes
                    # over and the tender is done again; once restarts run out the browser
                    # is dropped and this tender and the rest go on without documents.
                    while True:
                        try:
                            code, result = scrape_opportunity(client, tender, output_dir, vendorpanel)
                            break
                        except common.BrowserLostError as exc:
                            restart_browser(exc)
                    if result and result["session_lapsed"] and logged_in and relogins < MAX_RELOGINS:
                        # We were signed in, so the session probably lapsed.
                        relogins += 1
                        log.warning("       VendorPanel session lapsed -- signing in again "
                                    "(%d/%d) and retrying this tender", relogins, MAX_RELOGINS)
                        logged_in = sign_in_again()
                        if logged_in:
                            code, result = scrape_opportunity(client, tender, output_dir, vendorpanel)
                        else:
                            log.error("login: FAILED on re-login")
                            vendorpanel = None
                    if result:
                        results.append(result)
                    tender_codes.add(code)
                    all_codes.append(code)
                    code_index[reference] = len(all_codes) - 1
                    reporting.tender_line(log, index, len(tenders), reference, code)
                except httpx.HTTPStatusError as exc:
                    blocked = blocked_code(exc)
                    if blocked is not None:
                        log.error("HTTP %s from %s -- stopping the run",
                                  exc.response.status_code, exc.request.url)
                        return blocked, results
                    reporting.tender_failed(log, index, len(tenders), reference, exc)
                    tender_codes.add(common.TENDER_PARTIAL)
                    all_codes.append(common.TENDER_PARTIAL)
                except Exception as exc:
                    reporting.tender_failed(log, index, len(tenders), reference, exc)
                    tender_codes.add(common.TENDER_PARTIAL)
                    all_codes.append(common.TENDER_PARTIAL)
                time.sleep(PAUSE_SECONDS)

            if (have_credentials and start_status == "no_browser" and vendorpanel is None
                    and any(r.get("documents_gated") for r in results)):
                # Chrome would not start at the beginning. Now that the pages are read,
                # try again; the retry pass below then fetches the documents.
                for attempt in range(1, LATE_START_ATTEMPTS + 1):
                    wait = LATE_START_WAIT_SECONDS * attempt
                    log.warning("browser: trying to start it again in %ds (late attempt %d/%d)",
                                wait, attempt, LATE_START_ATTEMPTS)
                    time.sleep(wait)
                    start_status = start_browser()
                    if start_status != "no_browser":
                        break
                if start_status == "ok":
                    log.info("login: ok (browser started on a later attempt)")
                    login_failed = False
                    tender_codes.discard(common.SITE_LOGIN_FAILED)

            if vendorpanel is not None and logged_in:
                def relogin() -> bool:
                    nonlocal relogins, logged_in
                    if relogins >= MAX_RELOGINS:
                        return False
                    relogins += 1
                    log.warning("       VendorPanel session lapsed -- signing in again (%d/%d)",
                                relogins, MAX_RELOGINS)
                    return sign_in_again()

                for reference, code in retry_unfollowed(vendorpanel, results, relogin, restart=restart_for_retry_pass).items():
                    if reference in code_index:
                        all_codes[code_index[reference]] = code
                tender_codes = (set(all_codes) | ({common.SITE_LOGIN_FAILED} if login_failed else set())
                                | ({common.TENDER_PARTIAL} if listing_problems else set()))

            if have_credentials:
                needing = [r for r in results if r["documents_advertised"] > 0]
                followed = [r for r in needing if r["followed"]]
                if len(followed) == len(needing):
                    log.info("follow: %d/%d tender(s) with documents are followed", len(followed), len(needing))
                else:
                    missing = [r["tender_id"] for r in needing if not r["followed"]]
                    log.error("follow: only %d/%d tender(s) with documents are followed -- not followed: %s",
                              len(followed), len(needing), ", ".join(missing[:20]) + (" ..." if len(missing) > 20 else ""))

            reporting.diagnose(log, all_codes, logged_in=logged_in, partial_is_expected=not logged_in)

        return common.site_code_from(tender_codes), results

    except common.StructureChangedError as exc:
        log.error("structure change: %s", exc)
        return common.SITE_STRUCTURE_CHANGE, results
    except httpx.HTTPStatusError as exc:
        log.error("HTTP %s from %s", exc.response.status_code, exc.request.url)
        return blocked_code(exc) or common.SITE_TOTAL_FAILURE, results
    except httpx.TransportError as exc:
        log.error("could not connect: %s", exc)
        return common.SITE_TOTAL_FAILURE, results


@reporting.reported(SOURCE_ID.upper())
def run_scraper(limit: int = 0, output_dir: str = "tenders_data") -> common.ScrapeResult:
    """
    The pipeline's entry point. Returns (error code, site name, tenders scraped)
    and nothing else -- the tenders themselves are left in output_dir.
    """
    code, tenders = _scrape_site(limit, output_dir)
    return common.ScrapeResult(code, SOURCE_ID, len(tenders))


def main():
    reporting.configure_logging()
    run_scraper()  # no cap; for a capped local run use: python manager.py --local


if __name__ == "__main__":
    main()