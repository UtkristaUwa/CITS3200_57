"""
Web scraper for the Queensland Government tenders portal (QTenders).

Stage 1: walk every page of the open-tender search results and collect the link
to each tender's detail page, writing them to a .txt file so the extraction can
be verified.

Stage 2: fetch each of those detail pages and write one folder per tender
under tenders_data/<VP reference>/ containing:
    * <VP reference>.txt -- the tender's full detail content
    * documents.json     -- the attachments the page reports
    * the attachment files themselves, where they could be downloaded

The public VendorPanel preview prints only a document *count* -- filenames and
download links appear once a registered supplier account is signed in. The
manifest therefore records one placeholder entry per undownloadable attachment
so the shortfall is visible rather than silent.

    https://qtenders.hpw.qld.gov.au/search?keywords=&statuses=1&page=N&sortBy=Opens

Each result card's title is an <h4> containing a single <a> that points at the
tender's VendorPanel preview page, e.g.

    <h4 class="tw:text-lg ..."><a href="https://www.vendorpanel.com.au/
        PublicTenderPreviewPop.aspx?id=817f...s522442">Title</a></h4>

The href differs for every tender, so we select on structure (h4 > a), never on
the URL itself. The card also carries a "Reference number:" (e.g. VP522442),
which we capture because it will become the per-tender folder name in stage 2
(mirroring tenders_data/<RFx>/ used by the other scrapers).

Notes on the site:
    * The page is a Blazor app -- the tender cards are rendered client-side and
      are NOT in the raw HTML, so a plain HTTP/BeautifulSoup scrape returns
      nothing. A real browser is required.
    * Pagination is done with buttons, not links, but the ?page=N query
      parameter works when navigated to directly, which is what we do.
    * The pager prints "Showing page X of Y (Z total tenders)", which is how we
      learn the number of pages and verify each navigation actually landed.

Requirements:
    pip install seleniumbase

Usage:
    python web_scrapers/qld_qtenders/qld_qtenders.py             # both stages
    SKIP_COLLECT=1 python .../qld_qtenders.py   # reuse saved URLs, skip browser
    LIMIT=5 python .../qld_qtenders.py          # only fetch the first 5 tenders
    MAX_PAGES=2 python .../qld_qtenders.py      # only scan 2 search pages
    VISIBLE=1 python .../qld_qtenders.py        # show the browser window
"""

import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
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
    TenderRecord,
    download_document,
    sanitise_filename,
    tender_dir,
    write_manifest,
    write_tender_text,
)

URL_TEMPLATE = (
    "https://qtenders.hpw.qld.gov.au/search"
    "?keywords=&statuses=1&page={page}&sortBy=Opens"
)

OUTPUT_FILE = Path(__file__).with_name("qld_qtenders_urls.txt")

SOURCE_ID = "qld-qtenders"
VENDORPANEL_BASE = "https://www.vendorpanel.com.au"

# Attachment links on a VendorPanel page, for the (registered-supplier) case
# where they are rendered at all. Selected on the route, not on link text.
DOCUMENT_LINK_SELECTOR = (
    "a[href*='DownloadTenderDocument'], a[href*='DownloadDocument'], "
    "a[href*='GetTenderDocument']"
)

# The detail pages live on VendorPanel and are plain server-rendered ASP.NET --
# no JavaScript required -- so stage 2 fetches them over plain HTTP instead of
# with a browser, which is far faster than driving Chrome 125 times.
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
}

# Each result card's title link. Structural, so it survives the hashed hrefs and
# the Tailwind "tw:"-prefixed class names (which would need escaping in CSS).
LINK_SELECTOR = "h4 a[href]"

# "Showing page 1 of 7 (125 total tenders)"
PAGER_RE = re.compile(
    r"Showing\s+page\s+(\d+)\s+of\s+(\d+)\s*\(\s*([\d,]+)\s*total", re.I
)

# How long uc_open_with_reconnect stays disconnected while any bot check runs.
RECONNECT_TIME = 4


def build_driver(headless):
    """
    Create the UC-mode Chrome driver, container-aware.

    SeleniumBase auto-manages a matching chromedriver. Use `open_page()` (not
    driver.get) to navigate, so each load goes through the Cloudflare-bypassing
    reconnect handshake. See common.build_uc_driver for the container specifics.
    """
    return build_uc_driver(headless)


def open_page(driver, url):
    """Open a URL through UC mode's reconnect handshake."""
    driver.uc_open_with_reconnect(url, reconnect_time=RECONNECT_TIME)


def polite_pause(min_seconds=1.5, max_seconds=3.0):
    """Wait a randomised, human-like interval between page requests."""
    time.sleep(random.uniform(min_seconds, max_seconds))


def read_pager(driver):
    """Return (current_page, total_pages, total_tenders) or None if not rendered yet."""
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        return None
    match = PAGER_RE.search(body)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3).replace(",", ""))


def wait_for_page(driver, wait, expected_page):
    """
    Wait until the Blazor app has rendered the results for `expected_page`.

    Waiting on the pager's own "Showing page X of Y" text (rather than just the
    presence of links) guarantees we are not reading the previous page's cards.
    """
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, LINK_SELECTOR)))
    wait.until(
        lambda d: (read_pager(d) or (None,))[0] == expected_page
    )
    return read_pager(driver)


def load_page(driver, wait, page, attempts=3):
    """
    Navigate to a results page and wait for it to render, retrying on timeout.

    The Blazor app occasionally takes a long time to boot (or the site throttles
    us), which shows up as a render timeout. A retry with a longer settle time
    clears it, so one slow page doesn't abort the whole run.
    """
    page_url = URL_TEMPLATE.format(page=page)
    for attempt in range(1, attempts + 1):
        suffix = "" if attempt == 1 else f" (attempt {attempt}/{attempts})"
        print(f"Opening page {page}: {page_url}{suffix}")
        try:
            open_page(driver, page_url)
            return wait_for_page(driver, wait, page)
        except TimeoutException:
            if attempt == attempts:
                raise
            backoff = 5 * attempt
            print(f"  render timed out -- retrying in {backoff}s...")
            time.sleep(backoff)
    return None


def ref_from_url(url):
    """Fallback reference number: the trailing 's522442' in the href -> 'VP522442'."""
    match = re.search(r"s(\d+)\s*$", url)
    return f"VP{match.group(1)}" if match else ""


def links_on_current_page(driver):
    """Return [{'ref', 'title', 'url'}] for every tender card on the loaded page."""
    results = []
    for anchor in driver.find_elements(By.CSS_SELECTOR, LINK_SELECTOR):
        url = anchor.get_attribute("href")
        if not url:
            continue
        title = anchor.text.strip()

        # The reference number lives elsewhere in the same card; climb to the
        # card container and read it out of the card's text.
        ref = ""
        try:
            card = anchor.find_element(
                By.XPATH, './ancestor::div[contains(@class, "shadow-md")][1]'
            )
            match = re.search(r"Reference number:\s*(\S+)", card.text, re.I)
            if match:
                ref = match.group(1)
        except Exception:
            pass
        if not ref:
            ref = ref_from_url(url)

        results.append({"ref": ref, "title": title, "url": url})
    return results


def parse_detail(html):
    """
    Pull the fields out of a VendorPanel public tender preview page.

    The page is a flat sequence of rows shaped like:

        <div class='opportunityPreviewMaxHeading'>Buyer Details</div>   <- section
        <div class='opportunityPreviewInnerRow'>
          <div class='opportunityPreviewMinHeading'>Business Name</div> <- label
          <div class='opportunityPreviewContent'>Dept of ...</div>      <- value
        </div>

    Walking the headings in document order lets us keep each label under the
    section it belongs to, which matters because some labels (e.g. "Business
    Name") appear in more than one section.
    """
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one(".OpportunityPreviewNameRowTenderPublic")
    title = title_el.get_text(" ", strip=True) if title_el else ""

    fields = []  # (section, label, value) in document order
    section = "Tender Details"
    for el in soup.find_all(
        class_=["opportunityPreviewMaxHeading", "opportunityPreviewMinHeading"]
    ):
        classes = el.get("class", [])
        if "opportunityPreviewMaxHeading" in classes:
            section = el.get_text(" ", strip=True)
            continue
        value_el = el.find_next_sibling(class_="opportunityPreviewContent")
        if value_el is None:
            continue
        label = el.get_text(" ", strip=True).rstrip(":")
        value = value_el.get_text("\n", strip=True)
        fields.append((section, label, value))

    lookup = {label: value for _, label, value in fields}

    # The attachment count is a plain number, but tenders with no attachments
    # render "None..." instead of "0", so normalise it to an int.
    raw_documents = lookup.get("Documents", "")
    match = re.search(r"\d+", raw_documents)
    documents = int(match.group()) if match else 0

    return {
        "title": title,
        "ref": lookup.get("VP Reference #", ""),
        "buyer_ref": lookup.get("Buyers Reference #", ""),
        "documents": documents,
        "document_links": parse_document_links(soup),
        "fields": fields,
    }


def parse_document_links(soup):
    """
    Return the downloadable attachments rendered on a VendorPanel page.

    The public preview prints only a document *count* -- the filenames and
    links appear once a registered supplier account is signed in. This returns
    an empty list for the public page and the real attachments for a signed-in
    one, so the download path is the same either way.
    """
    documents = []
    for anchor in soup.select(DOCUMENT_LINK_SELECTOR):
        href = anchor.get("href")
        if not href:
            continue
        name = anchor.get("title") or anchor.get_text(" ", strip=True)
        documents.append(
            Document(
                file_name=sanitise_filename(name or "document.bin"),
                url=urljoin(VENDORPANEL_BASE, href),
            )
        )
    return documents


def format_detail(url, detail):
    """Render a parsed tender as a readable .txt document."""
    ref = detail["ref"] or "UNKNOWN"
    lines = [
        "=" * 80,
        f"QTENDERS (VENDORPANEL) DETAILS - {ref}",
        "=" * 80,
        "",
        f"Title      : {detail['title'] or '(no title)'}",
        f"Detail URL : {url}",
        f"Scraped At : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    current = None
    for section, label, value in detail["fields"]:
        if section != current:
            current = section
            lines += ["", section.upper() + ":", "-" * 40]
        if "\n" in value:  # multi-line blocks (descriptions, questions)
            lines.append(f"{label}:")
            lines += [f"    {line}" for line in value.splitlines()]
        else:
            lines.append(f"{label}: {value}")

    lines += [
        "",
        "-" * 40,
        f"NOTE: This tender lists {detail['documents']} attachment(s). Their "
        "filenames and downloads are not shown on the public preview page and "
        "require a VendorPanel login.",
        "",
        "=" * 80,
        "",
    ]
    return "\n".join(lines)


def fetch_detail(client, url, attempts=3):
    """GET a tender detail page, retrying briefly on transient failures."""
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, headers=HTTP_HEADERS, timeout=30.0)
            response.raise_for_status()
            return response.text
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(3 * attempt)
    return None


def save_details(tenders, output_dir=None):
    """
    Fetch every tender's detail page and write one folder per tender.

    Each folder gets <REF>.txt, a documents.json manifest and any attachments
    that could be downloaded. A tender that fails is recorded and skipped so
    one bad page cannot end the run.
    """
    records, failed = [], []

    with httpx.Client(follow_redirects=True) as client:
        for index, tender in enumerate(tenders, start=1):
            url = tender["url"]
            listing_ref = tender.get("ref") or ""
            try:
                html = fetch_detail(client, url)
                detail = parse_detail(html)

                # Prefer the reference printed on the detail page; fall back to
                # the one captured from the search listing.
                ref = detail["ref"] or listing_ref
                if not ref:
                    raise ValueError("no reference number found")

                folder = tender_dir(ref, output_dir)
                write_tender_text(folder, format_detail(url, detail))

                documents = detail["document_links"]
                for document in documents:
                    download_document(client, document, folder, headers=HTTP_HEADERS)
                    if document.error:
                        print(f"        WARNING: {document.file_name}: {document.error}")

                # The public preview advertises a count but no links. Record the
                # shortfall so the manifest shows what we know we are missing.
                advertised = detail["documents"]
                requires_login = advertised > len(documents)
                for position in range(len(documents), advertised):
                    documents.append(
                        Document(
                            file_name=f"(document {position + 1} of {advertised})",
                            error="requires a VendorPanel supplier login",
                        )
                    )

                record = TenderRecord(
                    reference=folder.name,
                    source_id=SOURCE_ID,
                    source_url=url,
                    documents=documents,
                    documents_require_login=requires_login,
                )
                write_manifest(folder, record)
                records.append(record)

                print(
                    f"[{index}/{len(tenders)}] {folder.name}  "
                    f"({record.documents_downloaded}/{advertised} attachment(s))"
                )
            except Exception as exc:
                failed.append((listing_ref or url, exc))
                print(f"[{index}/{len(tenders)}] WARNING {listing_ref or url}: "
                      f"{exc.__class__.__name__}: {exc}")

            time.sleep(random.uniform(0.4, 1.0))  # be polite between requests

    return records, failed


def read_saved_urls():
    """Re-read the tender list from the stage 1 .txt file (skips the browser)."""
    if not OUTPUT_FILE.exists():
        raise FileNotFoundError(
            f"{OUTPUT_FILE} not found -- run without SKIP_COLLECT=1 first."
        )
    text = OUTPUT_FILE.read_text(encoding="utf-8")
    refs = re.findall(r"^\[\d+\]\s*(\S+)", text, re.M)
    urls = re.findall(r"^\s*URL\s*:\s*(\S+)", text, re.M)
    return [{"ref": r, "url": u} for r, u in zip(refs, urls)]


def collect_tenders(headless, max_pages):
    """Stage 1: walk the search results in a browser and return the tender links."""
    print(f"Launching Chrome ({describe_browser_mode(headless)})...")
    driver = build_driver(headless)

    try:
        wait = WebDriverWait(driver, 40)

        pager = load_page(driver, wait, 1)
        if not pager:
            raise RuntimeError("Could not read the pager on page 1.")
        _, total_pages, total_tenders = pager
        print(f"Detected {total_pages} page(s), {total_tenders} total tenders.")

        if max_pages:
            total_pages = min(total_pages, max_pages)
            print(f"MAX_PAGES set -- only scraping {total_pages} page(s).")

        tenders = links_on_current_page(driver)
        print(f"  Page 1: {len(tenders)} link(s).")

        for page in range(2, total_pages + 1):
            polite_pause()
            try:
                load_page(driver, wait, page)
            except TimeoutException:
                print(f"  WARNING: page {page} did not render in time -- skipped.")
                continue
            found = links_on_current_page(driver)
            print(f"  Page {page}: {len(found)} link(s).")
            tenders.extend(found)

        # Drop any duplicates while preserving order.
        seen, unique = set(), []
        for tender in tenders:
            if tender["url"] not in seen:
                seen.add(tender["url"])
                unique.append(tender)
        duplicates = len(tenders) - len(unique)

        header = [
            "Queensland Government Tenders (QTenders) - open tender URLs",
            f"Source  : {URL_TEMPLATE.format(page='N')}",
            f"Scraped : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Pages   : {total_pages}",
            f"Reported: {total_tenders} total tenders",
            f"Found   : {len(unique)} unique tender URLs",
            "=" * 78,
            "",
        ]
        blocks = [
            f"[{i}] {t['ref'] or '(no ref)'}\n"
            f"    Title : {t['title'] or '(no title)'}\n"
            f"    URL   : {t['url']}"
            for i, t in enumerate(unique, start=1)
        ]
        OUTPUT_FILE.write_text(
            "\n".join(header) + "\n\n".join(blocks) + "\n", encoding="utf-8"
        )

        print(f"\nSaved {len(unique)} tender URL(s) to {OUTPUT_FILE}")
        if duplicates:
            print(f"({duplicates} duplicate link(s) removed.)")
        if len(unique) != total_tenders:
            print(
                f"NOTE: found {len(unique)} but the site reported {total_tenders}."
            )
        return unique

    finally:
        driver.quit()


def run_scraper(limit=0, output_dir=None, headless=True, max_pages=0,
                skip_collect=False):
    """
    Scrape open Queensland tenders into one directory each.

    `limit` and `max_pages` of 0 mean "no cap". Returns the list of
    TenderRecords written.
    """
    # Stage 1 -- gather the tender links (needs a browser: the search results are
    # rendered client-side by Blazor).
    if skip_collect:
        tenders = read_saved_urls()
        print(f"Reusing {len(tenders)} tender URL(s) from {OUTPUT_FILE.name}")
    else:
        tenders = collect_tenders(headless, max_pages)

    if limit:
        tenders = tenders[:limit]
        print(f"Limit set -- only fetching {len(tenders)} tender(s).")

    # Stage 2 -- fetch each detail page over plain HTTP (no browser needed) and
    # save it into its own folder.
    print(f"\nFetching {len(tenders)} tender detail page(s)...\n")
    records, failed = save_details(tenders, output_dir)

    print(f"\nDone. Saved {len(records)}/{len(tenders)} tender folder(s).")
    if failed:
        print(f"{len(failed)} failed:")
        for name, exc in failed:
            print(f"  - {name}: {exc.__class__.__name__}")
    return records


def main():
    visible = os.environ.get("VISIBLE", "").lower() in ("1", "true", "yes")
    run_scraper(
        limit=int(os.environ.get("LIMIT", "0")),           # 0 = every tender
        headless=not visible,
        max_pages=int(os.environ.get("MAX_PAGES", "0")),   # 0 = all pages
        skip_collect=os.environ.get("SKIP_COLLECT", "").lower() in ("1", "true", "yes"),
    )


if __name__ == "__main__":
    main()
