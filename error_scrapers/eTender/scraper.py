"""
error_scrapers/eTender/scraper.py

Scraper for QBuild eTender (etender.hpw.qld.gov.au), Queensland's building
and civil works tenders. Every function here is covered by
test_cases/test_eTender.py.

Per tender, saves into its own folder (via common.py's
tender_dir/save_page_text/add_source_url):
    __tender__<reference>.txt -- text from the tender's detail page

Plain HTTP, no browser, no login. The site is ASP.NET WebForms: the list pages
are walked with the pager's own postbacks (the hidden __VIEWSTATE and friends
are replayed), the detail pages are ordinary links.

Documents:
    There are none to collect. eTender only releases a tender's documents to a
    prequalified contractor who applies for that tender ("Apply for Tender"),
    and applying is not something a scraper should do. So this scraper is
    page-text only by design, and a tender with no documents is complete:
    every tender that parses is SITE_SUCCESS. Nothing here logs in or clicks
    anything that submits.
"""

import re
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from error_scrapers import common, reporting, run_notes

log = reporting.site_logger("QBUILD_ETENDER")

SOURCE_ID = "qbuild_etender"
BASE_URL = "https://etender.hpw.qld.gov.au"
LIST_URL = f"{BASE_URL}/public/TendersListingCurrent.aspx"
DETAIL_URL = f"{BASE_URL}/public/TenderDetailsActive.aspx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Every list row links to TenderDetailsActive.aspx?listingPage=Current&tid=N,
# twice (reference and description), so links are de-duplicated by tid.
DETAIL_LINK_SELECTOR = "table.tenderList a[href*='tid=']"
TID_PATTERN = re.compile(r"[?&]tid=(\d+)", re.I)
# "Page 1 of 2" under the list.
PAGE_COUNT_PATTERN = re.compile(r"Page\s+\d+\s+of\s+(\d+)", re.I)
# The pager's links: __doPostBack('<grid>','Page$2').
PAGER_PATTERN = re.compile(r"__doPostBack\('([^']+)','Page\$(\d+)'\)")

# The header table every detail page carries. If it, or the tender number in
# it, is missing on an otherwise real page, the site's structure has changed.
HEADER_SELECTOR = "table#tenderHeader"
DESCRIPTION_SELECTOR = "span[id$='TenderDisplayLabel']"
CONTACT_NAME_SELECTOR = "span[id$='ProjContact_contactName']"
CONTACT_PHONE_SELECTOR = "span[id$='ProjContact_phone']"

PAUSE_SECONDS = 0.3


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
# Parsing
# ---------------------------------------------------------------------------

def detail_url(tender_id: str) -> str:
    """A stable URL for one tender, built from its tid."""
    return f"{DETAIL_URL}?listingPage=Current&tid={tender_id}"


def parse_listing(html: str) -> list[str]:
    """Return every tender detail-page URL on one list page, once each."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.select(DETAIL_LINK_SELECTOR):
        match = TID_PATTERN.search(anchor.get("href") or "")
        if match:
            url = detail_url(match.group(1))
            if url not in links:
                links.append(url)
    return links


def total_pages(html: str) -> int | None:
    """How many pages the list says it has ("Page 1 of 2"), or None if it doesn't say."""
    match = PAGE_COUNT_PATTERN.search(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    return int(match.group(1)) if match else None


def next_page_form(html: str, page: int) -> dict | None:
    """
    The form data that asks for list page `page`: every hidden input on the
    page (__VIEWSTATE, __EVENTVALIDATION, ...) replayed, with the pager's
    postback target and argument filled in. None if the page has no link to it.
    """
    soup = BeautifulSoup(html, "html.parser")
    target = None
    for anchor in soup.select("tr.pager a[href]"):
        match = PAGER_PATTERN.search(anchor["href"])
        if match and int(match.group(2)) == page:
            target = match.group(1)
            break
    if target is None:
        return None
    form = soup.select_one("form#aspnetForm") or soup
    payload = {
        field["name"]: field.get("value", "")
        for field in form.select("input[type='hidden']")
        if field.get("name")
    }
    payload["__EVENTTARGET"] = target
    payload["__EVENTARGUMENT"] = f"Page${page}"
    return payload


def _clean(node) -> str | None:
    text = " ".join(node.get_text(" ", strip=True).split()) if node else ""
    return text or None


def parse_detail(html: str) -> tuple[dict, int]:
    """
    Pull a tender's own fields off its detail page.

    Returns (fields, status_code). If the header table or the tender number
    isn't found on an otherwise real page, returns ({}, SITE_STRUCTURE_CHANGE)
    rather than guessing at a different layout.
    """
    soup = BeautifulSoup(html, "html.parser")
    header = soup.select_one(HEADER_SELECTOR)
    if header is None:
        return {}, common.SITE_STRUCTURE_CHANGE

    # Label / value cell pairs, e.g. "No :" -> "3909852TIC (CO)1".
    pairs = {}
    for label in header.select("td.tenderHeaderLabel"):
        value = label.find_next_sibling("td", class_="tenderHeaderData")
        name = label.get_text(" ", strip=True).rstrip(":").strip()
        if name and value is not None:
            pairs[name] = _clean(value)

    reference = pairs.get("No")
    if not reference:
        return {}, common.SITE_STRUCTURE_CHANGE

    description = _clean(soup.select_one(DESCRIPTION_SELECTOR)) or pairs.get("Description")
    fields = {
        "reference": reference,
        # eTender gives one description string and no separate title.
        "title": description,
        "closing_date": pairs.get("Closing Date/Time"),
        "category": pairs.get("Tender Category"),
        "regions": pairs.get("Regions"),
        "federal_electorates": pairs.get("Federal Electorates"),
        "state_electorates": pairs.get("State Electorates"),
        "local_authorities": pairs.get("Local Authorities"),
        "pqc_level": pairs.get("PQC Level"),
        "contact_name": _clean(soup.select_one(CONTACT_NAME_SELECTOR)),
        "contact_phone": _clean(soup.select_one(CONTACT_PHONE_SELECTOR)),
    }
    return fields, common.SITE_SUCCESS


def format_detail_text(fields: dict, url: str) -> str:
    """Render the parsed fields as the tender's page-text file."""
    lines = [
        "=" * 80,
        f"QBUILD ETENDER: {fields.get('reference')}",
        "=" * 80,
        "",
    ]
    for key, value in fields.items():
        if value and key != "title":
            lines.append(f"{key}: {value}")
    if fields.get("title"):
        lines += ["", "DESCRIPTION:", "-" * 40, fields["title"]]
    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-tender and full-run orchestration
# ---------------------------------------------------------------------------

def scrape_opportunity(client, url: str, output_dir: str = "tenders_data") -> tuple[int, dict]:
    """
    Scrape one tender into its own folder.

    Returns (status_code, tender). `tender` is empty if the page could not be
    parsed. There are no documents to fetch (see the module docstring), so a
    tender whose page parses is a complete SITE_SUCCESS.
    """
    response = common.request_with_retries(client, "get", url, headers=HEADERS, timeout=30.0)
    response.raise_for_status()
    fields, code = parse_detail(response.text)
    if code != common.SITE_SUCCESS:
        return code, {}

    reference = fields["reference"]
    folder = common.tender_dir(reference, output_dir)
    common.save_page_text(folder, reference, format_detail_text(fields, url))
    common.add_source_url(folder, reference, url)

    reporting.documents_line(log, 0)
    return common.SITE_SUCCESS, {
        "tender_id": reference,
        "title": fields.get("title"),
        "folder": folder,
        "attachments": [],
        "source_url": url,
    }


def collect_all_listing_urls(client, limit: int = 0) -> list[str]:
    """
    Walk the list pages through the pager, stopping at the page count the first
    page reports, at a page that adds nothing new, or once `limit` is reached.
    `limit` of 0 means every current tender.

    Raises StructureChangedError if the very first page has no tenders:
    QBuild normally has current tenders, so an empty first page means the markup
    changed, not that there is nothing to scrape.
    """
    urls, page, pages = [], 1, None
    response = common.request_with_retries(client, "get", LIST_URL, headers=HEADERS, timeout=45.0)
    response.raise_for_status()
    while True:
        if pages is None:
            pages = total_pages(response.text)
        page_urls = [u for u in parse_listing(response.text) if u not in urls]
        if not page_urls:
            if page == 1:
                raise common.StructureChangedError("No tenders on the first list page.")
            break
        urls.extend(page_urls)
        if limit and len(urls) >= limit:
            return urls[:limit]
        if pages is not None and page >= pages:
            break
        form = next_page_form(response.text, page + 1)
        if form is None:
            if pages is not None:
                log.warning("listing: page %d has no link to page %d (of %d) -- stopping",
                            page, page + 1, pages)
            break
        page += 1
        time.sleep(PAUSE_SECONDS)
        # A POST is not retried on a timeout: the pager's state is per-page.
        response = client.post(LIST_URL, data=form, headers=dict(HEADERS, Referer=LIST_URL),
                               timeout=45.0)
        response.raise_for_status()
    return urls


def _scrape_site(limit: int = 0, output_dir: str = "tenders_data") -> tuple[int, list[dict]]:
    """
    Scrape every current QBuild tender. `limit` of 0 means every tender found.

    Returns (site_code, tenders). A 429 or 403 stops the run at once.
    """
    tenders: list[dict] = []
    try:
        with httpx.Client(follow_redirects=True) as client:
            urls = collect_all_listing_urls(client, limit)
            log.info("listing: %d tender link(s) found", len(urls))
            run_notes.note_total(len(urls))

            tender_codes = set()
            all_codes = []
            for index, url in enumerate(urls, start=1):
                try:
                    code, tender = scrape_opportunity(client, url, output_dir)
                    if tender:
                        tenders.append(tender)
                    tender_codes.add(code)
                    all_codes.append(code)
                    reporting.tender_line(log, index, len(urls),
                                          tender.get("tender_id", url) if tender else url, code)
                except httpx.HTTPStatusError as exc:
                    blocked = blocked_code(exc)
                    if blocked is not None:
                        log.error("HTTP %s from %s -- stopping the run",
                                  exc.response.status_code, exc.request.url)
                        return blocked, tenders
                    reporting.tender_failed(log, index, len(urls), url, exc)
                    tender_codes.add(common.TENDER_PARTIAL)
                    all_codes.append(common.TENDER_PARTIAL)
                except Exception as exc:
                    reporting.tender_failed(log, index, len(urls), url, exc)
                    tender_codes.add(common.TENDER_PARTIAL)
                    all_codes.append(common.TENDER_PARTIAL)
                time.sleep(PAUSE_SECONDS)

            reporting.diagnose(log, all_codes)

        return common.site_code_from(tender_codes), tenders

    except common.StructureChangedError as exc:
        log.error("structure change: %s", exc)
        return common.SITE_STRUCTURE_CHANGE, tenders
    except httpx.HTTPStatusError as exc:
        log.error("HTTP %s from %s", exc.response.status_code, exc.request.url)
        return blocked_code(exc) or common.SITE_TOTAL_FAILURE, tenders
    except httpx.TransportError as exc:
        log.error("could not connect: %s", exc)
        return common.SITE_TOTAL_FAILURE, tenders


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