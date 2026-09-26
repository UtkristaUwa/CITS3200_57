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
    The public VendorPanel preview prints only a document *count* -- the
    filenames and downloads appear only for a signed-in VendorPanel supplier.
    A tender whose preview advertises more documents than it links is
    reported TENDER_PARTIAL (page text saved, attachments missing).
"""

import html as html_lib
import re
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from error_scrapers import common

SOURCE_ID = "qld-qtenders"
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


def collect_all_tenders(client, limit: int = 0) -> list[dict]:
    """
    Walk every page of open tenders, stopping at the reported page count, at
    an empty page, or once `limit` is reached. `limit` of 0 means every
    open tender.
    """
    tenders, seen, page = [], set(), 1
    while True:
        response = client.post(SEARCH_API_URL, json=search_body(page), headers=HEADERS, timeout=30.0)
        response.raise_for_status()
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
        f"Detail URL: {tender.get('tenderPreviewUrl')}",
        "",
    ]
    summary = [
        ("VP Reference", tender.get("vpReference")),
        ("Buyers Reference", tender.get("buyersReference")),
        ("Agency", tender.get("departmentName") or tender.get("businessName")),
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
# Per-tender and full-run orchestration
# ---------------------------------------------------------------------------

def scrape_opportunity(client, tender: dict, output_dir: str = "tenders_data") -> tuple[int, dict]:
    """
    Scrape one tender (a search-API record) into its own folder.

    Returns (status_code, result). If the preview page can't be fetched, the
    search record alone is still saved as the page text and the tender is
    TENDER_PARTIAL; if it can be fetched but not parsed, nothing is saved
    and the code is SITE_STRUCTURE_CHANGE.
    """
    reference = tender["vpReference"]
    url = tender["tenderPreviewUrl"]

    detail, code, fetch_failed = {}, common.SITE_SUCCESS, False
    try:
        response = client.get(url, headers=HEADERS, timeout=30.0)
        response.raise_for_status()
        detail, code = parse_detail(response.text)
    except httpx.HTTPError as e:
        print(f"QTENDERS PREVIEW FAILED: {url}: {e}")
        fetch_failed = True
    if code != common.SITE_SUCCESS:
        return code, {}

    folder = common.tender_dir(reference, output_dir)
    common.save_page_text(folder, reference, format_detail_text(tender, detail))

    result = {
        "tender_id": reference,
        "title": tender.get("title"),
        "folder": folder,
        "attachments": [],
        "source_url": url,
        "documents_gated": False,
    }
    if fetch_failed:
        return common.TENDER_PARTIAL, result

    any_failed = False
    for document in detail["documents"]:
        try:
            attachment, extracted = common.download_attachment(
                client, document["url"], folder, document["file_name"], headers=HEADERS
            )
        except Exception as e:
            print(f"QTENDERS DOWNLOAD FAILED: {document['url']}: {e}")
            any_failed = True
            continue
        result["attachments"].append(attachment)
        any_failed = any_failed or not extracted

    result["documents_gated"] = detail["documents_advertised"] > len(detail["documents"])
    if any_failed or result["documents_gated"]:
        return common.TENDER_PARTIAL, result
    return common.SITE_SUCCESS, result


def run_scraper(limit: int = 0, output_dir: str = "tenders_data") -> tuple[int, list[dict]]:
    """
    Scrape every open QTenders tender. `limit` of 0 means every tender found.

    Returns (site_code, tenders) -- one entry per tender scraped, each
    listing the attachment files saved for it.
    """
    results: list[dict] = []
    try:
        with httpx.Client(follow_redirects=True) as client:
            tenders = collect_all_tenders(client, limit)

            tender_codes = set()
            for tender in tenders:
                try:
                    code, result = scrape_opportunity(client, tender, output_dir)
                    if result:
                        results.append(result)
                    tender_codes.add(code)
                except Exception as e:
                    print(f"QTENDERS TENDER FAILED: {tender.get('vpReference')}: {e}")
                    tender_codes.add(common.TENDER_PARTIAL)
                time.sleep(PAUSE_SECONDS)

        return common.site_code_from(tender_codes), results

    except common.StructureChangedError:
        return common.SITE_STRUCTURE_CHANGE, results
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            return common.SITE_RATE_LIMITED, results
        return common.SITE_TOTAL_FAILURE, results
    except httpx.TransportError:
        return common.SITE_TOTAL_FAILURE, results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scrape open QTenders tenders.")
    parser.add_argument("--limit", type=int, default=5, help="max tenders (0 = all)")
    parser.add_argument("--output-dir", default="tenders_data")
    args = parser.parse_args()

    code, tenders = run_scraper(limit=args.limit, output_dir=args.output_dir)
    print(f"QTenders run finished with code {code}, {len(tenders)} tenders scraped")


if __name__ == "__main__":
    main()
