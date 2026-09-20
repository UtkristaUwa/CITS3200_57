"""
error_scrapers/buy_nsw/scraper.py

Scraper for buy.nsw's Opportunities Hub. Written test-first, same
discipline as error_scrapers/grant_connect/scraper.py -- every function
here exists to satisfy a specific test in test_cases/test_buynsw.py.

Unlike GrantConnect, this source needs no login: every opportunity's
"Download opportunity package" link is a plain, publicly-reachable S3
URL. That one zip contains BOTH the real attachments AND an
auto-generated summary PDF of the whole detail page -- the summary must
be extracted into the tender's own page-text file and then discarded,
never treated as a real attachment.

Per opportunity, saves into its own folder (via common.py's
tender_dir/save_page_text/save_attachment/save_extracted_text):
    <opportunity_id>.txt   -- text from the summary PDF (or, if there
                              is no package at all, from the detail
                              page's own fields)
    <attachment files>     -- raw downloads, as-is
    <attachment>.txt       -- extracted text per attachment
"""

import io
import os
import re
import zipfile

import httpx
from bs4 import BeautifulSoup

from error_scrapers import common

BASE_URL = "https://buy.nsw.gov.au"
LIST_URL = f"{BASE_URL}/opportunity/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Every opportunity detail page link, from the public listing.
DETAIL_LINK_SELECTOR = "a[href*='/prcOpportunity/']"

# The container each Overview field (Managed, Type, Opportunity ID, ...)
# lives in on a detail page. If this selector finds nothing on an
# otherwise real-looking page, the site's structure has changed -- this
# is the exact class the structure-changed fixture renames to simulate
# that.
FIELD_ROW_SELECTOR = ".nsw-table-row"

# The opportunity package's auto-generated summary PDF is always named
# "opportunity-<opportunity-id>.pdf" -- everything else in the zip is a
# real attachment.
SUMMARY_PDF_PATTERN = re.compile(r"^opportunity-.*\.pdf$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_listing(html: str, base_url: str = BASE_URL) -> list[str]:
    """Return every opportunity detail-page URL on one listing page."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.select(DETAIL_LINK_SELECTOR):
        href = anchor.get("href")
        if not href:
            continue
        url = href if href.startswith("http") else f"{base_url}{href}"
        if url not in links:
            links.append(url)
    return links


def parse_detail(html: str) -> tuple[dict, int]:
    """
    Pull an opportunity's own fields off its detail page.

    Returns (fields, status_code). If the expected field container isn't
    found on an otherwise real page, returns ({}, SITE_STRUCTURE_CHANGE)
    rather than guessing at a different layout.
    """
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("h1 .nsw-main-h1-text")
    title = title_el.get_text(strip=True) if title_el else None

    rows = soup.select(FIELD_ROW_SELECTOR)
    if not rows:
        return {}, common.SITE_STRUCTURE_CHANGE

    fields = {"title": title}
    for row in rows:
        label_el = row.select_one(".col-lg-4")
        value_el = row.select_one(".col-lg-8")
        if label_el is None or value_el is None:
            continue
        label = label_el.get_text(strip=True).rstrip(":")
        value = value_el.get_text(" ", strip=True)
        fields[label] = value

    # Publish/Close/Estimated decision dates sit in a separate block
    # (span.font-weight--heading followed by a <p>), not one of the
    # nsw-table-row rows above -- but that same "font-weight--heading"
    # class is also used for the Overview row labels, so only headings
    # immediately followed by a <p> sibling are treated as dates here.
    for heading in soup.select(".font-weight--heading"):
        label = heading.get_text(strip=True).rstrip(":")
        value_el = heading.find_next_sibling("p")
        if value_el is not None and label not in fields:
            fields[label] = value_el.get_text(strip=True)

    fields["opportunity_id"] = fields.pop("Opportunity ID", None)
    fields["agency"] = fields.pop("Managed", None)
    fields["close_date"] = fields.pop("Close date", None)
    fields["publish_date"] = fields.pop("Publish date", None)

    return fields, common.SITE_SUCCESS


def format_detail_text(fields: dict) -> str:
    """
    Render the parsed fields as the opportunity's .txt file, for the
    (rare) case where there's no package to download at all -- e.g. a
    genuine "No files attached" opportunity.
    """
    lines = [
        "=" * 80,
        f"BUY.NSW OPPORTUNITY: {fields.get('title') or fields.get('opportunity_id')}",
        "=" * 80,
        "",
    ]
    for key, value in fields.items():
        if value:
            lines.append(f"{key}: {value}")
    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


def find_package_url(html: str, base_url: str = BASE_URL) -> str | None:
    """
    Locate the "Download opportunity package" link on a detail page.
    Returns None if there is nothing to download (a real, valid state --
    not every opportunity has attachments).
    """
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href]"):
        if "download opportunity package" in anchor.get_text(" ", strip=True).lower():
            href = anchor["href"]
            if href.startswith("//"):
                return "https:" + href
            if href.startswith("http"):
                return href
            return f"{base_url}{href}"
    return None


# ---------------------------------------------------------------------------
# Downloading + extraction
# ---------------------------------------------------------------------------

MAX_ZIP_DEPTH = 5


def _extract_zip_entries(zip_bytes: bytes, output_dir: str, opportunity_id: str,
                          attachments: list, depth: int = 0) -> tuple[str, bool]:
    """
    Walk every entry in a zip, recursing into any nested zip found
    (agencies sometimes bundle response templates as sub-zips inside the
    main opportunity package). Real files are appended to `attachments`
    in place; the summary PDF's text is returned rather than appended,
    since it is not a real attachment.

    Returns (page_text, any_failed). Depth is capped to guard against a
    maliciously or accidentally self-referential zip.
    """
    page_text = ""
    any_failed = False

    if depth > MAX_ZIP_DEPTH:
        return page_text, True

    extractors = {
        ".pdf": common.extract_pdf,
        ".docx": common.extract_docx,
        ".xlsx": common.extract_xlsx,
    }

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            file_name = os.path.basename(name)
            if not file_name:
                continue
            content = zf.read(name)
            extension = os.path.splitext(file_name)[1].lower()

            if extension == ".zip":
                nested_text, nested_failed = _extract_zip_entries(
                    content, output_dir, opportunity_id, attachments, depth + 1
                )
                # a summary pdf has never been seen nested inside another
                # zip in practice, but if one ever were, don't silently
                # drop it -- only the top-level call's page_text is used,
                # so surface it there.
                if nested_text and not page_text:
                    page_text = nested_text
                any_failed = any_failed or nested_failed
                continue

            if SUMMARY_PDF_PATTERN.match(file_name) and depth == 0:
                tmp_path = os.path.join(output_dir, file_name)
                with open(tmp_path, "wb") as f:
                    f.write(content)
                try:
                    page_text = common.extract_pdf(tmp_path)
                except common.ExtractionError:
                    page_text = ""
                    any_failed = True
                finally:
                    os.remove(tmp_path)
                common.save_page_text(output_dir, opportunity_id, page_text)
                continue

            raw_path = common.save_attachment(output_dir, file_name, content)
            extractor = extractors.get(extension)
            if extractor is not None:
                try:
                    text = extractor(raw_path)
                    common.save_extracted_text(output_dir, file_name, text)
                except common.ExtractionError:
                    any_failed = True
            attachments.append({"file_name": file_name, "content_type": None})

    return page_text, any_failed


def extract_package(zip_bytes: bytes, output_dir: str, opportunity_id: str) -> dict:
    """
    Unzip one opportunity's downloaded package. The one auto-generated
    summary PDF becomes the tender's own page-text file and is then
    discarded -- it is not a real attachment. Every other file is saved
    and extracted the same way GrantConnect's process_documents() does.
    Any nested zip (agencies sometimes bundle response templates this
    way) is recursed into rather than saved as an opaque, unreadable
    blob.

    Returns {"page_text": str, "attachments": list[dict], "any_failed": bool}.
    """
    attachments: list = []
    page_text, any_failed = _extract_zip_entries(
        zip_bytes, output_dir, opportunity_id, attachments
    )
    return {"page_text": page_text, "attachments": attachments, "any_failed": any_failed}


# ---------------------------------------------------------------------------
# Per-opportunity and full-run orchestration
# ---------------------------------------------------------------------------

def scrape_opportunity(client, url: str, output_dir: str = "tenders_data") -> tuple[int, dict]:
    """Scrape one opportunity into its own folder. Returns (status_code, tender)."""
    response = client.get(url, headers=HEADERS, timeout=30.0)
    response.raise_for_status()
    fields, code = parse_detail(response.text)
    if code != common.SITE_SUCCESS:
        return code, {}

    opportunity_id = fields.get("opportunity_id") or "UNKNOWN"
    folder = common.tender_dir(opportunity_id, output_dir)
    package_url = find_package_url(response.text)

    attachments = []
    if package_url:
        try:
            pkg_response = client.get(package_url, headers=HEADERS, timeout=60.0)
            pkg_response.raise_for_status()
            result = extract_package(pkg_response.content, folder, opportunity_id)
            attachments = result["attachments"]
            if result["any_failed"]:
                tender = {"title": fields.get("title"), "folder": folder,
                          "attachments": attachments, **fields}
                return common.TENDER_PARTIAL, tender
        except Exception:
            return common.TENDER_PARTIAL, {
                "title": fields.get("title"), "folder": folder, "attachments": [],
            }
    else:
        # No package at all is a legitimate state (a genuine "No files
        # attached" opportunity) -- save the fields we already have as
        # the tender's own page text instead.
        common.save_page_text(folder, opportunity_id, format_detail_text(fields))

    tender = {"title": fields.get("title"), "folder": folder,
              "attachments": attachments, **fields}
    return common.SITE_SUCCESS, tender


def _get_with_retry(client, url, *, retries=2, **kwargs):
    """
    buy.nsw occasionally answers a normal request with 202 Accepted and
    a body that isn't the real page -- seen live, gone on an immediate
    retry. Not a block (that would be 403/429), just a transient
    hiccup. Retry a couple of times before giving up.
    """
    last_response = None
    for attempt in range(retries + 1):
        response = client.get(url, **kwargs)
        if response.status_code == 200:
            return response
        last_response = response
    return last_response


def collect_all_listing_urls(client, limit: int = 0) -> list[str]:
    """
    Walk every page of the opportunity list, stopping when a page comes
    back with no new links (end of results) or once `limit` is reached.
    `limit` of 0 means walk every page.
    """
    urls, page = [], 1
    while True:
        response = _get_with_retry(
            client,
            LIST_URL,
            params={"query": "", "categories": "", "types": "", "area": "", "page": page},
            headers=HEADERS,
            timeout=30.0,
        )
        response.raise_for_status()
        page_urls = parse_listing(response.text)
        if not page_urls:
            break
        urls.extend(page_urls)
        if limit and len(urls) >= limit:
            return urls[:limit]
        page += 1
    return urls


def run_scraper(limit: int = 0, output_dir: str = "tenders_data") -> tuple[int, list[dict]]:
    """
    Scrape every current opportunity across every page of the listing.
    `limit` of 0 means every opportunity found.

    Returns (site_code, tenders) -- one entry per opportunity
    successfully scraped, each with the attachment files it saved.
    """
    tenders: list[dict] = []
    try:
        with httpx.Client(follow_redirects=True) as client:
            urls = collect_all_listing_urls(client, limit)

            tender_codes = set()
            for url in urls:
                try:
                    code, tender = scrape_opportunity(client, url, output_dir)
                    if tender:
                        tenders.append(tender)
                    if code != common.SITE_SUCCESS:
                        tender_codes.add(code)
                except Exception:
                    tender_codes.add(common.TENDER_PARTIAL)
                    continue

        if not tender_codes:
            return common.SITE_SUCCESS, tenders
        if common.SITE_STRUCTURE_CHANGE in tender_codes:
            return common.SITE_STRUCTURE_CHANGE, tenders
        return common.TENDER_PARTIAL, tenders

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            return common.SITE_RATE_LIMITED, tenders
        return common.SITE_TOTAL_FAILURE, tenders
    except (httpx.ConnectError, ConnectionError):
        return common.SITE_TOTAL_FAILURE, tenders


def main():
    import logging
    logging.basicConfig(level=logging.INFO)
    code, tenders = run_scraper(limit=2)
    print(f"buy.nsw run finished with code {code}, {len(tenders)} tenders scraped")


if __name__ == "__main__":
    main()