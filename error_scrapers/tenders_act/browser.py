"""
error_scrapers/tenders_act/browser.py

Tenders ACT blocks plain httpx requests with a 403 at the network
layer, even on the bare login page, before any login attempt --
confirmed live: the same request succeeds in a real browser but fails
identically from httpx regardless of headers. This is TLS/behavioural
fingerprinting a plain HTTP client can't replicate.

This module does the parts of the ACT flow that need a real browser
(everything, until proven otherwise -- see module-level NOTE below),
using SeleniumBase to match the browser-automation approach already
used elsewhere in this project (see web_scrapers/Dockerfile's Chrome
layer, built for the VIC/QLD scrapers).

Every page's HTML is handed to the same BeautifulSoup-based parsing
functions in scraper.py (parse_detail, parse_listing,
parse_download_form) -- nothing about how the HTML gets parsed
changes, only how it gets fetched.
"""

import os
import time

from seleniumbase import SB

from error_scrapers import common, reporting

log = reporting.site_logger("TENDERS_ACT")

BASE_URL = "https://www.tenders.act.gov.au"
LOGIN_URL = f"{BASE_URL}/login"
LIST_URL = f"{BASE_URL}/tenders/open"

from error_scrapers.tenders_act.scraper import (
    BotBlockedError, credentials, is_blocked, is_signed_in,
)

LOGIN_ERROR_TEXT = "Invalid username/password combination"

# How many times get() tries a page, and how long it waits for a selector.
GET_ATTEMPTS = 3
WAIT_TIMEOUT = 30

# ACT builds each tender's document zip on request; 60s was too short for big
# packages. The download has to start within DOWNLOAD_WAIT_SECONDS. Once a partial file is
# there it is given until it stalls (no change for DOWNLOAD_STALL_SECONDS) or
# DOWNLOAD_HARD_MAX_SECONDS in all: the large packages (33 documents) are slow,
# not broken, and used to be abandoned at 60s.
DOWNLOAD_WAIT_SECONDS = int(os.environ.get("ACT_DOWNLOAD_WAIT_SECONDS", "180"))
DOWNLOAD_STALL_SECONDS = int(os.environ.get("ACT_DOWNLOAD_STALL_SECONDS", "300"))
DOWNLOAD_HARD_MAX_SECONDS = int(os.environ.get("ACT_DOWNLOAD_HARD_MAX_SECONDS", "900"))


class BrowserSession:
    """
    Wraps one SeleniumBase browser instance for the whole ACT run.
    Use as a context manager so the browser always closes:

        with BrowserSession(download_dir="tenders_data") as session:
            ok = session.login()
            html = session.get(LIST_URL)
    """

    def __init__(self, download_dir: str = "tenders_data", headless: bool = True):
        self.download_dir = os.path.abspath(download_dir)
        os.makedirs(self.download_dir, exist_ok=True)
        self._headless = headless
        self._sb_cm = None
        self.sb = None

    def __enter__(self):
        # SeleniumBase always downloads clicked files into a fixed
        # "./downloaded_files/" folder relative to the working
        # directory -- there is no SB() parameter to redirect this
        # (confirmed: the location is hard-coded to avoid multi-run
        # conflicts). download_via_form() below moves the file out of
        # there into the tender's own folder afterward.
        self._sb_cm = SB(
            uc=True,  # undetected-chromedriver mode -- helps against
                      # exactly this kind of TLS/behavioural bot check
            headless=self._headless,
        )
        self.sb = self._sb_cm.__enter__()
        self._sb_downloads_dir = os.path.join(os.getcwd(), "downloaded_files")

        # Log the browser version once, so a Chrome/driver mismatch shows up
        # in the logs if it ever matters.
        try:
            caps = self.sb.driver.capabilities
            log.info("chrome %s", caps.get("browserVersion"))
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._sb_cm is not None:
            self._sb_cm.__exit__(exc_type, exc_val, exc_tb)

    def _debug_dump(self, label: str) -> None:
        """
        Print what the browser is currently looking at. The HTML is flattened
        onto one line so Cloud Logging keeps it in a single entry.
        """
        try:
            log.warning("debug %s title: %s", label, self.sb.get_title())
            log.warning("debug %s url: %s", label, self.sb.get_current_url())
            html = self.sb.get_page_source()[:1500].replace("\n", " ").replace("\r", " ")
            log.warning("debug %s html: %s", label, html)
        except Exception as e:
            log.warning("debug %s could not read page: %s", label, e)

    def _on_challenge_page(self) -> bool:
        try:
            return is_blocked(self.sb.get_title(), self.sb.get_page_source())
        except Exception:
            return False

    def get(self, url: str, wait_selector: str | None = None,
            attempts: int = GET_ATTEMPTS) -> str:
        """
        Navigate to url and return the rendered page's HTML. Retries, giving
        each attempt a longer reconnect, and dumps what the page looked like
        whenever an attempt fails.
        """
        last_err = None
        for attempt in range(1, attempts + 1):
            try:
                self.sb.uc_open_with_reconnect(url, reconnect_time=4 + 4 * attempt)
                if wait_selector:
                    self.sb.wait_for_element(wait_selector, timeout=WAIT_TIMEOUT)
                return self.sb.get_page_source()
            except Exception as e:
                last_err = e
                log.warning("get attempt %d/%d failed (%s) for %s",
                            attempt, attempts, type(e).__name__, url)
                self._debug_dump(f"attempt {attempt}")
                if self._on_challenge_page():
                    # Retrying the same page in the same browser has never cleared
                    # a challenge; only a fresh browser (the caller's retry) is worth it.
                    raise BotBlockedError(f"Cloudflare challenge instead of {url}") from e
        raise last_err

    def login(self) -> bool:
        """Fill and submit the supplier login form. Returns True on success."""
        username, password = credentials()
        if not username or not password:
            log.error("login: ACT_USERNAME %s, ACT_PASSWORD %s -- credentials are not in "
                      "the environment. Check the job's env vars / Secret Manager mapping.",
                      "set" if username else "MISSING", "set" if password else "MISSING")
            return False
        log.info("login: ACT_USERNAME set, ACT_PASSWORD set -- submitting the form")

        self.get(LOGIN_URL, wait_selector="#supplierUsername")
        self.sb.type("#supplierUsername", username)
        self.sb.type("#supplierPassword", password)
        self.sb.click("#supplierLoginForm button[type='submit']")

        # Wait up to ~10s for a definite outcome: an error message means the
        # site refused the sign-in; the Log Out link means we are in. Leaving
        # the /login URL is not proof on its own, so it is not accepted.
        for _ in range(10):
            time.sleep(1)
            page = self.sb.get_page_source()
            if LOGIN_ERROR_TEXT in page:
                log.error("login: the site refused the username/password")
                return False
            if is_signed_in(page):
                return True

        self._debug_dump("login not confirmed")
        return False

    def download_via_form(self, download_docs_url: str, doc_ids: list[str]) -> str:
        """
        Open the download-docs page, click Download Documents, wait for
        the resulting zip to land in SeleniumBase's fixed downloads
        folder, then move it into download_dir. Returns the moved
        file's path.
        """
        self.get(download_docs_url, wait_selector="#downloadButton")
        os.makedirs(self._sb_downloads_dir, exist_ok=True)
        before = set(os.listdir(self._sb_downloads_dir))
        self.sb.click("#downloadButton")
        name = common.wait_for_download(
            self._sb_downloads_dir, before, DOWNLOAD_WAIT_SECONDS,
            stall_seconds=DOWNLOAD_STALL_SECONDS, hard_max=DOWNLOAD_HARD_MAX_SECONDS)
        if name:
            src = os.path.join(self._sb_downloads_dir, name)
            dst = os.path.join(self.download_dir, name)
            os.replace(src, dst)
            return dst
        raise TimeoutError(
            f"Download did not complete (nothing started within {DOWNLOAD_WAIT_SECONDS}s, "
            f"or it stalled for {DOWNLOAD_STALL_SECONDS}s, or took over {DOWNLOAD_HARD_MAX_SECONDS}s)")