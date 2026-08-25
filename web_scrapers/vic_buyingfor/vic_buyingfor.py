"""
Web scraper for the Victorian Government "Buying for Victoria" tenders portal.

Two-stage scrape:
  1. Walk every page of https://www.tenders.vic.gov.au/tenders/open (paginated
     via the <div class="paging"> ?page=N links) and collect *only* the link to
     each tender's detail page, together with its RFx number. These are held in
     memory -- nothing is written yet.
  2. Visit each collected link and save the full text of the tender's detail
     page to  tenders_data/<RFx>/<RFx>.txt , creating one folder per tender
     (e.g. tenders_data/PROCF22-000236/). Attachment files are downloaded into
     the same folder by a later step.

Anti-bot note:
    The site sits behind Cloudflare, which blocks ordinary Selenium (and plain
    HTTP requests) with an "Attention Required!" challenge -- typically on the
    *second* request once it has fingerprinted the automated ChromeDriver.
    We therefore drive the page with SeleniumBase's UC (undetected) mode and
    open each page with `uc_open_with_reconnect`, which disconnects the
    automation channel while Cloudflare runs its check and reconnects once the
    real page has loaded. Requests are also spaced out with small randomised
    pauses so we behave like a person, not a scraper.

    Cloudflare also weighs IP reputation: a normal office/residential IP passes
    easily, while shared datacentre/VPN IPs get challenged harder. Run this from
    a normal network for best results.

Requirements:
    pip install seleniumbase

Usage:
    python web_scrapers/vic_buyingfor/vic_buyingfor.py           # headless (default)
    VISIBLE=1 python web_scrapers/vic_buyingfor/vic_buyingfor.py  # show the window
    LIMIT=3 python web_scrapers/vic_buyingfor/vic_buyingfor.py    # only first 3 (testing)
"""

import math
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

from seleniumbase import Driver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

URL = "https://www.tenders.vic.gov.au/tenders/open"

# One folder per tender lives under the repo-root tenders_data/ directory
# (this file is at <repo>/web_scrapers/vic_buyingfor/vic_buyingfor.py).
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "tenders_data"

# The page's own class is "tender-table" (hyphen). We also match the
# underscore spelling just in case the markup ever changes.
TABLE_SELECTOR = "div.tender-table, div.tender_table"

# How long uc_open_with_reconnect stays disconnected while Cloudflare runs its
# check. A few seconds is enough for the challenge to clear on a trusted IP.
RECONNECT_TIME = 5


class BlockedError(RuntimeError):
    """Raised when Cloudflare (or similar) serves a block/challenge page."""


def build_driver(headless):
    """
    Create a SeleniumBase UC-mode Chrome driver.

    UC mode strips the automation fingerprints Cloudflare uses to detect plain
    Selenium, and SeleniumBase auto-manages a matching chromedriver. Use
    `open_page()` (not driver.get) to navigate, so each load goes through the
    Cloudflare-bypassing reconnect handshake.
    """
    return Driver(uc=True, headless=headless)


def open_page(driver, url):
    """Open a URL through UC mode's Cloudflare-bypassing reconnect handshake."""
    driver.uc_open_with_reconnect(url, reconnect_time=RECONNECT_TIME)


def polite_pause(min_seconds=1.5, max_seconds=3.5):
    """Wait a randomised, human-like interval between page requests."""
    time.sleep(random.uniform(min_seconds, max_seconds))


def looks_blocked(driver):
    """Return True if the current page is a Cloudflare (or similar) block page."""
    title = (driver.title or "").lower()
    if "attention required" in title or "just a moment" in title:
        return True
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return False
    return "you have been blocked" in body or "cloudflare" in title


def determine_page_count(driver):
    """
    Work out how many result pages there are from the <div class="paging"> block.

    Primary strategy: parse the "Records: 1 - 25 of 63" summary and divide the
    total by the page size -- this stays correct even when the pager truncates
    its list of page-number links for large result sets.

    Falls back to the highest visible page-number link, then to 1.
    """
    pagers = driver.find_elements(By.CSS_SELECTOR, "div.paging")
    if not pagers:
        return 1
    text = pagers[0].text

    match = re.search(r"(\d+)\s*-\s*(\d+)\s+of\s+(\d+)", text)  # "1 - 25 of 63"
    if match:
        first, last, total = (int(g) for g in match.groups())
        per_page = max(last - first + 1, 1)
        return max(math.ceil(total / per_page), 1)

    numbers = [int(n) for n in re.findall(r"\b(\d+)\b", text)]
    return max(numbers) if numbers else 1


def sanitise_rfx(rfx, fallback):
    """Turn an RFx number into a safe folder name (e.g. 'A26/6' -> 'A26_6')."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", rfx).strip("_")
    return safe or fallback


def links_on_current_page(driver, wait, page):
    """
    Return [{'rfx', 'url'}] for every tender row on the currently loaded results
    page. Only the RFx number (for the folder name) and the detail-page link are
    taken -- not the other row details.
    """
    try:
        table_div = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, TABLE_SELECTOR))
        )
    except TimeoutException:
        if looks_blocked(driver):
            raise BlockedError(
                f"Listing page {page} was blocked by Cloudflare "
                f"(title: {driver.title!r})."
            ) from None
        raise

    results = []
    for row in table_div.find_elements(By.CSS_SELECTOR, "table tbody tr"):
        anchors = row.find_elements(By.CSS_SELECTOR, "a.tenderRowTitle")
        if not anchors:
            continue
        url = anchors[0].get_attribute("href")
        code_el = row.find_elements(
            By.CSS_SELECTOR, "td.tender-code-state span.tablesaw-cell-content b"
        )
        rfx = code_el[0].text.strip() if code_el else ""
        results.append({"rfx": rfx, "url": url})

    print(f"  Page {page}: found {len(results)} tender link(s).")
    return results


def collect_all_links(driver, wait):
    """Walk every results page and return all {'rfx', 'url'} tender links."""
    print(f"Opening {URL}")
    open_page(driver, URL)
    total_pages = determine_page_count(driver)
    print(f"Detected {total_pages} page(s) of results.")

    links = links_on_current_page(driver, wait, 1)  # page 1 already loaded
    for page in range(2, total_pages + 1):
        polite_pause()
        page_url = f"{URL}?page={page}"
        print(f"Opening page {page}: {page_url}")
        open_page(driver, page_url)
        links.extend(links_on_current_page(driver, wait, page))
    return links


def page_full_text(driver):
    """Return the main text content of the currently loaded detail page."""
    mains = driver.find_elements(By.CSS_SELECTOR, "main")
    element = mains[0] if mains else driver.find_element(By.TAG_NAME, "body")
    return element.text.strip()


def save_tender(driver, wait, index, total, tender):
    """Open one tender's detail page and save its full text to its own folder."""
    url = tender["url"]
    fallback = re.sub(r"\W+", "_", url.split("id=")[-1]) or f"tender_{index}"
    print(f"[{index}/{total}] {tender['rfx'] or fallback} -> {url}")

    open_page(driver, url)
    try:
        wait.until(lambda d: (d.title or "").startswith("Display Tender"))
    except TimeoutException:
        if looks_blocked(driver):
            raise BlockedError(
                f"Detail page for {tender['rfx'] or fallback} was blocked by "
                f"Cloudflare (title: {driver.title!r})."
            ) from None
        raise

    rfx = sanitise_rfx(tender["rfx"], fallback)
    folder = OUTPUT_DIR / rfx
    folder.mkdir(parents=True, exist_ok=True)

    content = "\n".join(
        [
            "=" * 80,
            f"VICTORIAN GOVERNMENT TENDER - {tender['rfx'] or rfx}",
            "=" * 80,
            "",
            f"Detail URL : {url}",
            f"Scraped At : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "-" * 80,
            page_full_text(driver),
            "",
            "=" * 80,
            "",
        ]
    )
    out_file = folder / f"{rfx}.txt"
    out_file.write_text(content, encoding="utf-8")
    print(f"        saved {out_file.relative_to(OUTPUT_DIR.parent)}")


def main():
    # Headless by default. Cloudflare is more likely to challenge a headless
    # browser, but UC mode's reconnect handshake handles the check. If you do get
    # blocked, set VISIBLE=1 to run with a real window, which Cloudflare trusts more.
    visible = os.environ.get("VISIBLE", "").lower() in ("1", "true", "yes")
    headless = not visible
    limit = int(os.environ.get("LIMIT", "0"))  # 0 = all tenders

    print(f"Launching Chrome ({'headless' if headless else 'visible window'})...")
    driver = build_driver(headless)

    try:
        wait = WebDriverWait(driver, 30)

        # Stage 1: collect every tender link (held in memory only).
        links = collect_all_links(driver, wait)
        if limit:
            links = links[:limit]
        print(f"\nCollected {len(links)} tender link(s). Saving detail pages...\n")

        # Stage 2: visit each link and save its detail page into its own folder.
        saved, failed = 0, []
        for i, tender in enumerate(links, start=1):
            try:
                polite_pause()
                save_tender(driver, wait, i, len(links), tender)
                saved += 1
            except Exception as exc:  # keep going if one tender fails
                failed.append((tender.get("rfx") or tender["url"], exc))
                print(f"        WARNING: skipped ({exc.__class__.__name__}: {exc})")

        print(f"\nDone. Saved {saved}/{len(links)} tenders into {OUTPUT_DIR}")
        if failed:
            print(f"{len(failed)} failed:")
            for name, exc in failed:
                print(f"  - {name}: {exc.__class__.__name__}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
