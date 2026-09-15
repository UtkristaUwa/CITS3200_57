"""
Web scraper for the Victorian Government "Buying for Victoria" tenders portal.

Two-stage scrape:
  1. Walk every page of https://www.tenders.vic.gov.au/tenders/open (paginated
     via the <div class="paging"> ?page=N links) and collect *only* the link to
     each tender's detail page, together with its RFx number. These are held in
     memory -- nothing is written yet.
  2. Visit each collected link and write one folder per tender under
     tenders_data/<RFx>/ (e.g. tenders_data/PROCF22-000236/) containing:
       * <RFx>.txt      -- the full text of the tender's detail page
       * tender.json    -- the tender in ingestion's 20-field shape
       * the document files themselves, where they could be downloaded

Documents:
    Buying for Victoria lists each attachment's name, version and size to
    anonymous visitors but only renders the download link for a signed-in
    session ("You must be logged in to download documents"). The scraper always
    records the full document list in tender.json, and downloads whatever
    links are present -- so pointing it at an authenticated browser profile
    starts pulling the files with no further change.

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
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from web_scrapers.common import (
    Document,
    build_uc_driver,
    describe_browser_mode,
    download_document,
    sanitise_filename,
    tender_dir,
    write_tender_record,
    write_tender_text,
)
from web_scrapers.tender_record import (
    build_record,
    classify_category,
    classify_status,
)

URL = "https://www.tenders.vic.gov.au/tenders/open"
BASE_URL = "https://www.tenders.vic.gov.au"
SOURCE_ID = "vic-buyingfor"

# The "Specification Documents" section. Every attachment is one li.specDoc,
# whose span.specName holds the filename -- and, for a logged-in session, the
# download anchor. Anonymously there is no anchor, only the name/version/size.
SPEC_LIST_SELECTOR = "#specsList li.specDoc"

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
    Create the UC-mode Chrome driver, container-aware.

    SeleniumBase auto-manages a matching chromedriver. Use `open_page()` (not
    driver.get) to navigate, so each load goes through the Cloudflare-bypassing
    reconnect handshake. See common.build_uc_driver for the container specifics.
    """
    return build_uc_driver(headless)


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
        results.append(parse_listing_row(row, anchors[0]))

    print(f"  Page {page}: found {len(results)} tender link(s).")
    return results


def _first_text(element, selector):
    """Text of the first match for `selector`, or "" if there is none."""
    found = element.find_elements(By.CSS_SELECTOR, selector)
    return found[0].text.strip() if found else ""


def parse_listing_row(row, anchor):
    """
    Pull one results row apart.

    The row carries the title, issuing agency, status, tender type and both
    dates. The detail page repeats none of that, so anything not captured here
    is lost -- which is why the row is parsed rather than just its link taken.
    """
    code_cell = row.find_elements(By.CSS_SELECTOR, "td.tender-code-state")
    status = type_ = ""
    if code_cell:
        status = _first_text(code_cell[0], "span.tender-row-state")
        # The type is the trailing line of the cell, after the code and status.
        lines = [line.strip() for line in code_cell[0].text.splitlines() if line.strip()]
        if lines:
            type_ = lines[-1]
            if type_ in (status, _first_text(code_cell[0], "span.tablesaw-cell-content b")):
                type_ = ""

    agency = ""
    for detail in row.find_elements(By.CSS_SELECTOR, "span.line-item-detail"):
        text = detail.text.strip()
        if text.lower().startswith("issued by:"):
            agency = text.split(":", 1)[1].strip()

    return {
        "rfx": _first_text(row, "td.tender-code-state span.tablesaw-cell-content b"),
        "url": anchor.get_attribute("href"),
        "title": anchor.text.strip(),
        "issuing_agency": agency,
        "status": status,
        "type": type_,
        "opening_date": _first_text(row, "span.opening_date"),
        "closing_date": _first_text(row, "span.closing_date"),
    }


def parse_detail_fields(html):
    """
    Pull the structured fields off a tender's detail page.

    The "General" block is a stack of label/value rows rather than a table, so
    the label column is read and paired with its sibling. Returns a dict keyed
    by the portal's own labels ("Type", "Status", "Number", "Region(s)", ...)
    plus the title, agency, description and first contact.
    """
    soup = BeautifulSoup(html, "html.parser")
    fields = {}

    for row in soup.select("#opportunityGeneralDetails div.row"):
        columns = row.find_all("div", recursive=False)
        if len(columns) < 2:
            continue
        label = columns[0].get_text(" ", strip=True).rstrip(":")
        if label:
            fields[label] = columns[1].get_text(" ", strip=True)

    title_element = soup.select_one("#tenderTitle")
    fields["title"] = title_element.get_text(" ", strip=True) if title_element else ""

    # "Issued By\n<Agency>" sits in a bold div in the header.
    agency = ""
    header = soup.select_one("#opportunityHeader")
    if header:
        for block in header.select("div.weight-bold"):
            text = block.get_text("\n", strip=True)
            if text.lower().startswith("issued by"):
                parts = [line for line in text.splitlines() if line.strip()]
                agency = parts[-1].strip() if len(parts) > 1 else ""
    fields["issuing_agency"] = agency

    description = soup.select_one("#tenderDescription div.col-12")
    fields["description"] = (
        description.get_text(" ", strip=True) if description else ""
    )

    contact = soup.select_one("#opportunityContacts div.contact")
    if contact:
        items = [li.get_text(" ", strip=True) for li in contact.select("li")]
        fields["contact_name"] = items[0].replace("(Enquiries)", "").strip() if items else ""
        mail = contact.select_one("a[href^='mailto:']")
        fields["contact_email"] = (
            mail.get("href").split(":", 1)[1].strip() if mail else ""
        )
    return fields


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


def parse_specification_documents(html, base_url=None):
    """
    Return the Documents listed in the tender's "Specification Documents" block.

    Each li.specDoc gives the filename (span.specName), and its nested <ul> the
    version and the size as printed on the page. The download href only exists
    for a logged-in session, so `url` is None for an anonymous scrape and the
    document is still recorded -- we know it exists even when we cannot fetch it.
    """
    base_url = base_url or BASE_URL
    soup = BeautifulSoup(html, "html.parser")
    documents = []

    for item in soup.select(SPEC_LIST_SELECTOR):
        name_element = item.select_one("span.specName")
        if name_element is None:
            continue

        anchor = name_element.find("a", href=True)
        file_name = (anchor or name_element).get_text(" ", strip=True)
        if not file_name:
            continue

        version = size = None
        for line in item.select("ul li"):
            text = line.get_text(" ", strip=True)
            match = re.match(r"(Version\s+.+)", text, re.I)
            if match:
                version = match.group(1)
                continue
            match = re.search(r"\(([\d.,]+\s*[KMG]?B)\)", text, re.I)
            if match:
                size = match.group(1)

        documents.append(
            Document(
                file_name=sanitise_filename(file_name),
                url=urljoin(base_url, anchor["href"]) if anchor else None,
                version=version,
                size_label=size,
                error=None if anchor else "login required (no download link shown)",
            )
        )
    return documents


def specs_require_login(html):
    """True if the specifications block says a login is needed to download."""
    return "must be logged in to download" in BeautifulSoup(
        html, "html.parser"
    ).get_text(" ", strip=True).lower()


def download_client(driver):
    """
    An httpx client carrying the browser's cookies.

    Downloads go over plain HTTP rather than through Chrome so files land in the
    tender's own folder instead of Chrome's download directory. Copying the
    session cookies across means an authenticated browser session stays
    authenticated for the file requests.
    """
    client = httpx.Client(follow_redirects=True, timeout=60.0)
    for cookie in driver.get_cookies():
        client.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))
    return client


def save_tender(driver, wait, index, total, tender, output_dir=None):
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

    folder = tender_dir(tender["rfx"], output_dir, fallback=fallback)
    rfx = folder.name

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
    write_tender_text(folder, content)

    html = driver.page_source
    fields = parse_detail_fields(html)
    documents = parse_specification_documents(html)
    requires_login = specs_require_login(html)

    downloadable = [d for d in documents if d.url]
    if downloadable:
        with download_client(driver) as client:
            for document in downloadable:
                download_document(client, document, folder)
                if document.error:
                    print(f"        WARNING: {document.file_name}: {document.error}")
                polite_pause(0.3, 0.8)

    record = build_record(
        source_id=SOURCE_ID,
        source_url=url,
        title=fields.get("title") or tender.get("title") or tender.get("rfx"),
        reference=fields.get("Number") or tender.get("rfx"),
        issuing_agency=fields.get("issuing_agency") or tender.get("issuing_agency"),
        category=classify_category(fields.get("Type"), tender.get("type")),
        status=classify_status(fields.get("Status"), tender.get("status")),
        # Neither date is on the detail page -- only the results row has them.
        publish_date=tender.get("opening_date"),
        closing_date=tender.get("closing_date"),
        location=fields.get("Region(s)") or fields.get("Region"),
        description=fields.get("description"),
        contact_name=fields.get("contact_name"),
        contact_email=fields.get("contact_email"),
        documents=documents,
        requires_login=requires_login,
        raw_extra={
            "portal_status_label": fields.get("Status") or tender.get("status"),
            "portal_type_label": fields.get("Type") or tender.get("type"),
            "unspsc_category": fields.get("UNSPSC"),
        },
    )
    write_tender_record(folder, record)
    print(
        f"        saved {folder.name}/ "
        f"({sum(1 for d in documents if d.downloaded)}/{len(documents)} document(s))"
    )
    return record


def run_scraper(limit=0, output_dir=None, headless=True):
    """
    Scrape open Victorian tenders into one directory each.

    `limit` of 0 means every tender. Returns the list of tender records; a
    tender that fails is logged and skipped so one bad page cannot end the run.
    """
    print(f"Launching Chrome ({describe_browser_mode(headless)})...")
    driver = build_driver(headless)

    try:
        wait = WebDriverWait(driver, 30)

        # Stage 1: collect every tender link (held in memory only).
        links = collect_all_links(driver, wait)
        if limit:
            links = links[:limit]
        print(f"\nCollected {len(links)} tender link(s). Saving detail pages...\n")

        # Stage 2: visit each link and save its detail page into its own folder.
        records, failed = [], []
        for i, tender in enumerate(links, start=1):
            try:
                polite_pause()
                records.append(save_tender(driver, wait, i, len(links), tender, output_dir))
            except Exception as exc:  # keep going if one tender fails
                failed.append((tender.get("rfx") or tender["url"], exc))
                print(f"        WARNING: skipped ({exc.__class__.__name__}: {exc})")

        print(f"\nDone. Saved {len(records)}/{len(links)} tender folder(s).")
        if failed:
            print(f"{len(failed)} failed:")
            for name, exc in failed:
                print(f"  - {name}: {exc.__class__.__name__}")
        return records

    finally:
        driver.quit()


def main():
    # Headless by default. Cloudflare is more likely to challenge a headless
    # browser, but UC mode's reconnect handshake handles the check. If you do get
    # blocked, set VISIBLE=1 to run with a real window, which Cloudflare trusts more.
    visible = os.environ.get("VISIBLE", "").lower() in ("1", "true", "yes")
    limit = int(os.environ.get("LIMIT", "0"))  # 0 = all tenders
    run_scraper(limit=limit, headless=not visible)


if __name__ == "__main__":
    main()
