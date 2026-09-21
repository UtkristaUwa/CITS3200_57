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

NOTE on the open question: it's not yet confirmed whether the site's
block is a one-time "prove you're a browser" check (in which case a
browser login's cookies could be handed to a fast httpx client for
the rest of the scrape) or checked on every request (in which case
the browser must do the whole scrape). This module assumes the
stricter case -- the browser does everything -- since that's
guaranteed to work if login works. If real runs show plain httpx
requests succeed once the browser's cookies are transplanted onto an
httpx.Client, that's a real, safe speed optimisation to make later;
don't assume it works without testing it directly.
"""

import os
import time

from seleniumbase import SB

BASE_URL = "https://www.tenders.act.gov.au"
LOGIN_URL = f"{BASE_URL}/login"
LIST_URL = f"{BASE_URL}/tenders/open"

USERNAME = os.environ.get("ACT_USERNAME")
PASSWORD = os.environ.get("ACT_PASSWORD")

LOGIN_ERROR_TEXT = "Invalid username/password combination"

# How many times get() tries a page, and how long it waits for a selector.
GET_ATTEMPTS = 3
WAIT_TIMEOUT = 30


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
            print(f"[ACT] chrome {caps.get('browserVersion')}", flush=True)
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
            print(f"[ACT DEBUG] {label} title: {self.sb.get_title()}", flush=True)
            print(f"[ACT DEBUG] {label} url: {self.sb.get_current_url()}", flush=True)
            html = self.sb.get_page_source()[:1500].replace("\n", " ").replace("\r", " ")
            print(f"[ACT DEBUG] {label} html: {html}", flush=True)
        except Exception as e:
            print(f"[ACT DEBUG] {label} could not read page: {e}", flush=True)

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
                print(
                    f"[ACT DEBUG] get attempt {attempt}/{attempts} failed "
                    f"({type(e).__name__}) for {url}",
                    flush=True,
                )
                self._debug_dump(f"attempt {attempt}")
        raise last_err

    def login(self) -> bool:
        """Fill and submit the supplier login form. Returns True on success."""
        if not USERNAME or not PASSWORD:
            print("[ACT] ACT_USERNAME / ACT_PASSWORD are not set", flush=True)
            return False

        self.get(LOGIN_URL, wait_selector="#supplierUsername")
        self.sb.type("#supplierUsername", USERNAME)
        self.sb.type("#supplierPassword", PASSWORD)
        self.sb.click("#supplierLoginForm button[type='submit']")

        # Wait up to ~10s for a definite outcome instead of a blind sleep:
        # an error message means failure, the login form disappearing means
        # we got in.
        for _ in range(10):
            time.sleep(1)
            if LOGIN_ERROR_TEXT in self.sb.get_page_source():
                return False
            if not self.sb.is_element_visible("#supplierLoginForm"):
                return True

        # No clear signal. If we've left the /login URL, treat it as success.
        if "/login" not in self.sb.get_current_url():
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
        for _ in range(60):
            new_files = set(os.listdir(self._sb_downloads_dir)) - before
            real_files = [f for f in new_files if not f.endswith(".crdownload")]
            if real_files:
                src = os.path.join(self._sb_downloads_dir, real_files[0])
                dst = os.path.join(self.download_dir, real_files[0])
                os.replace(src, dst)
                return dst
            time.sleep(1)
        raise TimeoutError("Download did not complete within 60 seconds")