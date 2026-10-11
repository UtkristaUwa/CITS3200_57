"""
error_scrapers/tenders_wa/scraper.py

Scraper for Tenders WA (tenders.wa.gov.au), the Western Australian public
sector tenders bulletin board. Every function here is covered by
test_cases/test_tenders_wa.py.

Per tender, saves into its own folder (via common.py's
tender_dir/save_page_text/unpack_zip):
    __tender__<reference>.txt -- text from the detail page's own fields
    <attachment files>        -- the specification documents
    <attachment>.txt          -- extracted text per attachment

Plain HTTP, no browser, no login. The whole list of advertised tenders comes
back on one page. Documents are downloaded the way an anonymous visitor does it:

    detail page -> "Download Now"            (GET)
                -> "Download for Information Only"   (POST, no sign-in)
                -> "Download Documents"      (POST; every file is ticked)  -> a zip

Nothing here signs in or submits a response. "Download for Information Only"
is the site's own anonymous option: Tenders WA will not send us addenda
notices, which is fine for a scraper that re-reads the tender each night.

Politeness: a pause between requests, one session, and a 429 or 403 ends the
run at once -- we back off rather than retry or work around it.
"""

import os
import re
import tempfile
import time
import zipfile
from email.message import Message
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from error_scrapers import common, reporting, run_notes

log = reporting.site_logger("TENDERS_WA")

SOURCE_ID = "tenders_wa"
BASE_URL = "https://www.tenders.wa.gov.au"
HOME_URL = f"{BASE_URL}/watenders/index.action"
# The "Tenders > Advertised" menu link. The live link also carries a session
# token (CSRFNONCE), so we read it off the home page; this is the fallback.
LIST_URL = f"{BASE_URL}/watenders/tender/search/tender-search.action?action=advanced-tender-search-open-tender"
LIST_ACTION_PATTERN = re.compile(r"action=advanced-tender-search-open-tender(?:&|$)")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# One row per tender in the results table.
LIST_ROW_SELECTOR = "table#tenderSearchResultsTable tbody tr"
DETAIL_LINK_SELECTOR = "a[href*='tender-details.action?id=']"
TENDER_ID_PATTERN = re.compile(r"[?&]id=(\d+)")
# The site's own practice tender. It sits in the live list; it is not a real one.
#The content area of a details page (leaves out the site menu and the login panel in the full page text)
PAGE_CONTENT_SELECTOR = "div.pcontent"
DEMO_AGENCY = "demonstration agency"

# Detail page.
TITLE_SELECTOR = "div.pcontent table.subtitle th.h2"
DOCUMENTS_SELECTOR = "table#documents"
DOWNLOAD_LINK_SELECTOR = "a[href*='downloadspecs.action']"
DESCRIPTION_SELECTOR = "textarea#desc"

# Download pages.
INFO_ONLY_BUTTON_TEXT = "information only"
INFO_ONLY_ACTION = "downloadInfoOnly"
DOWNLOAD_ACTION = "downloadViaHTML"
DOCUMENT_BOX_PREFIX = "documentRequested"

PAUSE_SECONDS = 0.5


class DownloadPageChanged(Exception):
    """A download step's page no longer has the button or form we expect."""


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
# Listing
# ---------------------------------------------------------------------------

def detail_url(tender_id: str) -> str:
    """A stable URL for one tender, built from its id (the live links carry a session token)."""
    return f"{BASE_URL}/watenders/tender/display/tender-details.action?id={tender_id}&action=display-tender-details"


def find_list_url(home_html: str, home_url: str = HOME_URL) -> str:
    """The "Advertised" link as the site serves it, or the plain URL if it isn't there."""
    for anchor in BeautifulSoup(home_html, "html.parser").select("a[href]"):
        if LIST_ACTION_PATTERN.search(anchor["href"]):
            return urljoin(home_url, anchor["href"])
    return LIST_URL


def parse_listing(html: str) -> list[str]:
    """Return every real tender's detail-page URL on the list page, once each."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for row in soup.select(LIST_ROW_SELECTOR):
        cells = row.find_all("td", recursive=False)
        if cells and cells[0].get_text(strip=True).lower() == DEMO_AGENCY:
            continue
        anchor = row.select_one(DETAIL_LINK_SELECTOR)
        match = TENDER_ID_PATTERN.search(anchor.get("href") or "") if anchor else None
        if match:
            url = detail_url(match.group(1))
            if url not in links:
                links.append(url)
    return links


def collect_all_listing_urls(client, limit: int = 0) -> list[str]:
    """
    Every advertised tender, from the one list page. `limit` of 0 means all.

    Raises StructureChangedError if the page has no tenders: Tenders WA always
    has advertised tenders, so an empty list means the markup changed (or we
    were sent somewhere that is not the list), not that there is nothing to scrape.
    """
    home = common.request_with_retries(client, "get", HOME_URL, headers=HEADERS, timeout=30.0)
    home.raise_for_status()
    list_url = find_list_url(home.text, str(home.url))

    time.sleep(PAUSE_SECONDS)
    response = common.request_with_retries(client, "get", list_url, headers=HEADERS, timeout=60.0)
    response.raise_for_status()
    urls = parse_listing(response.text)
    if not urls:
        raise common.StructureChangedError("No tenders on the Advertised list page.")
    return urls[:limit] if limit else urls


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------

def _clean(text) -> str | None:
    text = " ".join((text or "").split())
    return text or None


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _labelled_values(soup) -> dict:
    """Every "Label:" cell and the cell beside it (Status, Number, UNSPSC, Region/s...)."""
    values = {}
    for label in soup.select("span.LIST_TITLE"):
        text = label.get_text(" ", strip=True)
        cell = label.find_parent("td")
        value = cell.find_next_sibling("td") if cell else None
        if text.endswith(":") and value is not None:
            # A cell can hold several entries on separate lines (the regions);
            # keep them apart instead of running them together.
            values[text.rstrip(":").strip()] = ", ".join(value.stripped_strings) or None
    return values


def _contacts(soup) -> list[str]:
    contacts = []
    for role in soup.select("table.contacts div.LIST_TITLE"):
        details = {}
        inner = role.find_next("table")
        for row in inner.find_all("tr") if inner else []:
            cells = row.find_all("td")
            if len(cells) >= 2:
                details[cells[0].get_text(strip=True).lower()] = _clean(cells[1].get_text(" ", strip=True))
        parts = [details.get(k) for k in ("person", "phone", "email") if details.get(k)]
        contacts.append(f"{role.get_text(strip=True)}: {', '.join(parts)}" if parts else role.get_text(strip=True))
    return contacts


def _description(soup) -> str | None:
    """The description is HTML, escaped inside a <textarea> for the page's editor."""
    box = soup.select_one(DESCRIPTION_SELECTOR)
    if box is None:
        return None
    text = BeautifulSoup(box.get_text(), "html.parser").get_text("\n", strip=True)
    return text or None


def _documents(soup) -> list[str]:
    """The advertised specification documents: "<name> (<version>)" each."""
    heading = soup.select_one(DOCUMENTS_SELECTOR)
    table = heading.find_next_sibling("table") if heading else None
    names = []
    for row in table.find_all("tr") if table else []:
        title = row.select_one("span.LIST_TITLE")
        if title is None:
            continue
        version = row.select_one("span.SMALL_GRAY")
        suffix = version.get_text(strip=True) if version and version.get_text(strip=True).startswith("(") else ""
        names.append(_clean(f"{title.get_text(' ', strip=True)} {suffix}"))
    return names


# Sections the parser already reads into their own fields.
HANDLED_SECTIONS = {"contacts", "description", "specification documents"}
# Lines on the page that are controls, not information.
CONTROL_LINE = re.compile(r"submit (an electronic|a) response|must be logged in|^help icon$", re.I)


def _is_section_start(element) -> bool:
    if getattr(element, "name", None) is None:
        return False
    return (element.name == "hr" or element.get("id") == "Document Viewer Links"
            or element.find(class_="h2") is not None or "h2" in (element.get("class") or []))


def _section_lines(elements) -> list[str]:
    """The readable lines of some page elements: table rows kept on one line,
    and any HTML escaped inside a <textarea> unwrapped to text."""
    lines = []
    for element in elements:
        copy = BeautifulSoup(str(element), "html.parser")
        for tag in copy(["script", "style"]):
            tag.decompose()
        for box in copy.find_all("textarea"):
            box.replace_with(BeautifulSoup(box.get_text(), "html.parser").get_text("\n", strip=True))
        for row in copy.find_all("tr"):
            if row.find("table"):
                continue
            cells = [_clean(c.get_text(" ", strip=True)) for c in row.find_all(["td", "th"], recursive=False)]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                row.replace_with(copy.new_string(("%s: %s" % (cells[0], " | ".join(cells[1:]))) + "\n"))
        lines += [_clean(line) for line in copy.get_text("\n", strip=True).split("\n")]
    return [line for line in lines if line]


def _other_sections(soup) -> dict:
    """
    Every titled section of the page that has no field of its own (Site Visit,
    the Responses instructions...): {heading: text}. A section is a heading
    (class h2) with the table holding it and whatever follows it up to the next
    heading.
    """
    pcontent = soup.select_one("div.pcontent")
    if pcontent is None:
        return {}
    containers = []
    for heading in pcontent.select(".h2"):
        table = heading.find_parent("table") or heading
        if table not in containers:
            containers.append(table)

    sections = {}
    for index, table in enumerate(containers):
        if index == 0:  # the title block
            continue
        heading = _clean(table.select_one(".h2").get_text(" ", strip=True))
        if not heading or heading.lower() in HANDLED_SECTIONS:
            continue
        elements = [table]
        for sibling in table.find_next_siblings():
            if _is_section_start(sibling):
                break
            elements.append(sibling)
        lines = [line for line in _section_lines(elements)
                 if line != heading and not line.startswith("Closes ") and not CONTROL_LINE.search(line)]
        if lines:
            sections[heading] = "\n".join(lines)
    return sections


def parse_detail(html: str, url: str = BASE_URL) -> tuple[dict, int]:
    """
    Pull a tender's own fields off its detail page.

    Returns (fields, status_code). If the title block or the tender number
    isn't found on an otherwise real page, returns ({}, SITE_STRUCTURE_CHANGE)
    rather than guessing at a different layout. `fields` also carries
    `documents` (the advertised files) and `download_url` (None when the
    tender offers no download).
    """
    soup = BeautifulSoup(html, "html.parser")
    title_cell = soup.select_one(TITLE_SELECTOR)
    labelled = _labelled_values(soup)
    reference = labelled.get("Number")
    if title_cell is None or not reference:
        return {}, common.SITE_STRUCTURE_CHANGE

    block = title_cell.find("div")
    issued_by = block.find("span") if block else None
    title = _clean(next(iter(block.stripped_strings), "")) if block else None
    agency = _clean(issued_by.get_text(" ", strip=True)) if issued_by else None
    if agency and agency.lower().startswith("issued by"):
        agency = _clean(agency[len("issued by"):])

    kind = soup.select_one("table.nospace2 strong")
    closing = soup.find("span", style=re.compile("C00000", re.I))
    link = soup.select_one(f"{DOCUMENTS_SELECTOR} {DOWNLOAD_LINK_SELECTOR}")

    fields = {
        "reference": reference,
        "title": title,
        "type": _clean(kind.get_text(" ", strip=True)) if kind else None,
        "status": labelled.get("Status"),
        "issued_by": agency,
        "closes": re.sub(r"^Closes\s+", "", _clean(closing.get_text(" ", strip=True)) or "") or None if closing else None,
        "unspsc": labelled.get("UNSPSC"),
        "regions": labelled.get("Region/s"),
        "contacts": _contacts(soup),
        "description": _description(soup),
        "documents": _documents(soup),
        "sections": _other_sections(soup),
        "download_url": urljoin(url, link["href"]) if link and link.get("href") else None,
    }
    for label, value in labelled.items():
        if label not in ("Number", "Status", "UNSPSC", "Region/s"):
            fields.setdefault(_key(label), value)
    return fields, common.SITE_SUCCESS


def format_detail_text(fields: dict, url: str) -> str:
    """Render the parsed fields as the tender's page-text file."""
    lines = [
        "=" * 80,
        f"TENDERS WA: {fields.get('title') or fields.get('reference')}",
        "=" * 80,
        "",
    ]
    for key, value in fields.items():
        if key in ("description", "download_url", "title", "sections") or not value:
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines += [f"  - {item}" for item in value]
        else:
            lines.append(f"{key}: {value}")
    if fields.get("description"):
        lines += ["", "DESCRIPTION:", "-" * 40, fields["description"]]
    for heading, text in (fields.get("sections") or {}).items():
        lines += ["", f"{heading.upper()}:", "-" * 40, text]
    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Download steps
# ---------------------------------------------------------------------------

def _form_payload(form) -> dict:
    return {
        field["name"]: field.get("value", "")
        for field in form.select("input[type='hidden'][name]")
    }


def parse_info_only_form(html: str, page_url: str) -> tuple[str, dict] | None:
    """
    The "Download for Information Only" step (the anonymous option): the URL to
    post to and the form data, with the action set to the button's own. None if
    the page has no such button.
    """
    soup = BeautifulSoup(html, "html.parser")
    for form in soup.select("form[name='form']"):
        button = form.find("input", attrs={"value": re.compile(INFO_ONLY_BUTTON_TEXT, re.I)})
        if button is not None:
            payload = _form_payload(form)
            payload["action"] = INFO_ONLY_ACTION
            return urljoin(page_url, form.get("action") or ""), payload
    return None


def parse_documents_form(html: str, page_url: str) -> tuple[str, dict, list[str]] | None:
    """
    The "Download Documents" step: the URL to post to, the form data (every
    document ticked, as the page offers them) and the file names. None if the
    page has no document form.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form[name='form'][action*='request-spec-docs']")
    if form is None:
        return None
    boxes = [b for b in form.select("input[type='checkbox']")
             if (b.get("name") or "").startswith(DOCUMENT_BOX_PREFIX)]
    if not boxes:
        return None
    payload = _form_payload(form)
    payload["action"] = DOWNLOAD_ACTION
    names = []
    for box in boxes:
        payload[box["name"]] = box.get("value") or "true"
        label = form.find("label", attrs={"for": box.get("id")}) if box.get("id") else None
        names.append(_clean(label.get_text(" ", strip=True)) if label else box["name"])
    return urljoin(page_url, form.get("action") or ""), payload, names


def _filename_from(response) -> str | None:
    header = response.headers.get("content-disposition")
    if not header:
        return None
    message = Message()
    message["content-disposition"] = header
    return message.get_filename()


def _download_package(client, url: str, payload: dict, referer: str, folder: str) -> tuple[str, str | None, str]:
    """
    Post the document form and stream the answer to a temporary file in the
    folder. Returns (path, file name from the headers, content type). A timeout,
    dropped connection or 500/502/503/504 is tried once more; a web page in
    place of the files raises DocumentGatedError and is not retried.
    """
    def fetch_once():
        path = None
        try:
            with client.stream("POST", url, data=payload, timeout=180.0,
                               headers=dict(HEADERS, Referer=referer)) as response:
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").lower()
                if "text/html" in content_type:
                    raise common.DocumentGatedError(f"{url} returned a web page, not the documents")
                with tempfile.NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as handle:
                    path = handle.name
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                return path, _filename_from(response), content_type
        except BaseException:
            # A half-written file must never sit in the folder looking like an attachment.
            if path and os.path.exists(path):
                os.remove(path)
            raise

    return common.retry_transient(fetch_once, url)


def _store_package(path: str, file_name: str | None, content_type: str, folder: str) -> tuple[list[dict], bool]:
    """
    Unpack what came back. Tenders WA hands over a zip; if it ever sends one
    file on its own (a Content-Disposition name that is not .zip), that file is
    stored as it is. Returns (attachments, any_failed).
    """
    is_bundle = (
        "zip" in content_type and not (file_name or "").lower().endswith(
            (".docx", ".xlsx", ".pptx"))
    ) or (file_name or "").lower().endswith(".zip") or (file_name is None and zipfile.is_zipfile(path))
    if is_bundle:
        return common.unpack_zip(path, folder)

    target = common.unique_path(folder, common.sanitise_filename(file_name or "document"))
    os.replace(path, target)
    saved_name = os.path.basename(target)
    if os.path.getsize(target) == 0:
        os.remove(target)
        run_notes.note_reason(run_notes.EMPTY, saved_name)
        return [], True
    size = os.path.getsize(target)
    extracted = common.extract_attachment_text(folder, saved_name)
    if not os.path.exists(target):
        return [], True  # dropped (password protected): not an attachment
    return [{"file_name": saved_name, "content_type": content_type or None, "size_bytes": size}], not extracted


def download_documents(client, download_url: str, folder: str) -> tuple[list[dict], bool]:
    """
    Run the three download steps for one tender. Returns (attachments, any_failed).

    Raises DownloadPageChanged if a step's page is not the one we expect,
    DocumentGatedError if the files come back as a web page, and lets HTTP and
    network errors through for the caller to judge.
    """
    page = common.request_with_retries(client, "get", download_url, headers=HEADERS, timeout=30.0)
    page.raise_for_status()
    info_only = parse_info_only_form(page.text, str(page.url))
    if info_only is None:
        raise DownloadPageChanged("the Download Documents page has no 'Download for Information Only' button")
    post_url, payload = info_only

    time.sleep(PAUSE_SECONDS)
    listing = common.request_with_retries(
        client, "post", post_url, data=payload, headers=dict(HEADERS, Referer=str(page.url)), timeout=30.0)
    listing.raise_for_status()
    documents = parse_documents_form(listing.text, str(listing.url))
    if documents is None:
        raise DownloadPageChanged("the information-only step did not lead to a list of documents")
    files_url, files_payload, _names = documents

    time.sleep(PAUSE_SECONDS)
    path, file_name, content_type = _download_package(
        client, files_url, files_payload, str(listing.url), folder)
    try:
        return _store_package(path, file_name, content_type, folder)
    finally:
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# Per-tender and full-run orchestration
# ---------------------------------------------------------------------------

def scrape_opportunity(client, url: str, output_dir: str = "tenders_data") -> tuple[int, dict]:
    """
    Scrape one tender into its own folder.

    Returns (status_code, tender). `tender` is empty if the page could not be
    parsed. The page text is saved before any download, so a failed download
    never costs the tender its text.
    """
    response = common.request_with_retries(client, "get", url, headers=HEADERS, timeout=30.0)
    response.raise_for_status()
    fields, code = parse_detail(response.text, str(response.url))
    if code != common.SITE_SUCCESS:
        return code, {}

    reference = fields["reference"]
    folder = common.tender_dir(reference, output_dir)
    common.save_page_text(folder, reference, format_detail_text(fields, url))
    common.append_full_page_text(folder, reference, response.text, PAGE_CONTENT_SELECTOR)
    common.add_source_url(folder, reference, url)

    tender = {
        "tender_id": reference,
        "title": fields.get("title"),
        "folder": folder,
        "attachments": [],
        "source_url": url,
    }

    advertised = len(fields["documents"])
    if not fields["download_url"]:
        if advertised:
            log.info("       documents: %d listed but the page offers no download", advertised)
        else:
            reporting.documents_line(log, 0)
        return common.SITE_SUCCESS, tender

    try:
        attachments, any_failed = download_documents(client, fields["download_url"], folder)
    except DownloadPageChanged as exc:
        log.error("       documents: %s -- the download pages may have changed", exc)
        return common.SITE_STRUCTURE_CHANGE, tender
    except common.DocumentGatedError as exc:
        log.warning("       documents: %s", exc)
        run_notes.note_reason(run_notes.PORTAL)
        return common.TENDER_PARTIAL, tender
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and blocked_code(exc) is not None:
            raise  # the site says slow down or go away: stop the run
        log.warning("       download FAILED: %s: %s", fields["download_url"], exc)
        run_notes.note_reason(run_notes.DOWNLOAD, "document package")
        return common.TENDER_PARTIAL, tender

    tender["attachments"] = attachments
    reporting.documents_line(log, len(attachments), advertised or None)
    if len(attachments) < advertised:
        run_notes.note_reason(run_notes.DOWNLOAD, f"{advertised - len(attachments)} document(s) missing")
        any_failed = True
    return (common.TENDER_PARTIAL if any_failed else common.SITE_SUCCESS), tender


def _scrape_site(limit: int = 0, output_dir: str = "tenders_data") -> tuple[int, list[dict]]:
    """
    Scrape every advertised Tenders WA tender. `limit` of 0 means every tender found.

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