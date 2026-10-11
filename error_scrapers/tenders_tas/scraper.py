"""
error_scrapers/tenders_tas/scraper.py

Scraper for Tasmanian Government eTendering (tenders.tas.gov.au), "Open For Bids".
Every function here is covered by test_cases/test_tenders_tas.py.

Per tender, saves into its own folder (via common.py's
tender_dir/save_page_text/download_attachment):
    __tender__<Unique Tender ID>.txt -- the listing row plus the detail page's
                                        fields, then a FULL PAGE TEXT block
    <attachment files>               -- one file per document the tender lists
    <attachment>.txt                 -- extracted text per attachment

Plain HTTP, no browser. The site is ASP.NET MVC.

Listing:
    /OpenForBids/List/Public/ClosingDate is one page with every open tender,
    grouped under "Closes: <date>" heading rows. There is no pager.

Documents:
    The Downloadables page (/OpenForBids/Downloadables/<id>) lists each file
    as its own link (/Tender/DownloadFile/<fileId>); there is no zip. Each file
    is fetched on its own. Downloading needs a registered supplier account, so
    set TAS_USERNAME / TAS_PASSWORD. Without them, a tender with documents is
    TENDER_PARTIAL; with them, a failed login is SITE_LOGIN_FAILED and the page
    text is still scraped. The site records every download against the account.

Nothing here lodges a submission or touches the "Lodge a Submission" pages.
"""

import os
import re
import time
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from error_scrapers import common, reporting, run_notes

from dotenv import load_dotenv
load_dotenv()

log = reporting.site_logger("TENDERS_TAS")

SOURCE_ID = "tenders_tas"
BASE_URL = "https://www.tenders.tas.gov.au"
LOGIN_URL = f"{BASE_URL}/Account/Logon"
LIST_URL = f"{BASE_URL}/OpenForBids/List/Public/ClosingDate"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Listing.
LIST_TABLE_SELECTOR = "div#content table"
DETAIL_LINK_SELECTOR = "a[href*='/OpenForBids/Details/']"
DETAIL_ID_PATTERN = re.compile(r"/OpenForBids/Details/(\d+)")
# The site's own practice notice. It sits in the live list; it is not a real tender.
DEMO_TITLE_PREFIX = "demonstration notice"

# Detail page.
PAGE_CONTENT_SELECTOR = "#content"
FIELD_ROW_SELECTOR = "div.itemrow"
REFERENCE_LABEL = "Unique Tender ID"
TITLE_LABEL = "Procurement Title"
# Fields that only hold a link to another page of the site.
SKIPPED_LABELS = {"Download Files"}
# Words that only join a link to the text after it ("... or contact:").
CONNECTOR_LINES = {"or contact:", "or deliver to:"}
# Labels whose text normally repeats another field (the contact details).
REPEATING_LABELS = {"Documents are available from"}

# Documents page.
DOCUMENT_LINK_SELECTOR = "a[href*='/Tender/DownloadFile/']"

# Login.
LOGIN_ERROR_TEXT = "user name or password provided is incorrect"
LOGOUT_LINK_SELECTOR = "a[href='/Account/LogOff']"

PAUSE_SECONDS = 1.0
MAX_RELOGINS = 3


def credentials():
    """Return (username, password) from the environment, or (None, None)."""
    return (
        os.environ.get("TAS_USERNAME") or None,
        os.environ.get("TAS_PASSWORD") or None,
    )


def blocked_code(exc: httpx.HTTPStatusError) -> int | None:
    """429 -> SITE_RATE_LIMITED, 403 -> SITE_BOT_BLOCKED, anything else None."""
    status = exc.response.status_code
    if status == 429:
        return common.SITE_RATE_LIMITED
    if status == 403:
        return common.SITE_BOT_BLOCKED
    return None


class SessionLapsedError(Exception):
    """The site showed its sign-in form in place of the page (or file) asked for."""


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def find_login_form(html: str):
    """The form that takes a password (the page also has forgotten-password boxes)."""
    for form in BeautifulSoup(html or "", "html.parser").select("form"):
        if form.select_one("input[name='Password']"):
            return form
    return None


def is_signed_in(html: str) -> bool:
    """True if the page carries the signed-in header link (Log off)."""
    return BeautifulSoup(html or "", "html.parser").select_one(LOGOUT_LINK_SELECTOR) is not None


def login_failed(html: str) -> bool:
    """True if `html` is the login page with the site's "incorrect" message."""
    soup = BeautifulSoup(html or "", "html.parser")
    errors = " ".join(e.get_text(" ", strip=True) for e in soup.select(".validation-summary-errors"))
    return LOGIN_ERROR_TEXT in errors.lower()


def login(client: httpx.Client) -> bool:
    """
    Sign in. True on success, False when the site rejects the credentials (or
    there are none). A page that is neither the success nor the failure page
    raises StructureChangedError. A failed login is never retried: repeated
    bad logins can lock the account.
    """
    username, password = credentials()
    if not (username and password):
        log.error("login: credentials are not in the environment (TAS_USERNAME / TAS_PASSWORD)")
        return False

    page = common.request_with_retries(client, "get", LOGIN_URL, headers=HEADERS, timeout=30.0)
    page.raise_for_status()
    form = find_login_form(page.text)
    if form is None:
        raise common.StructureChangedError("No login form found on the Tasmania login page.")

    # The anti-forgery token is in the form; its cookie is already in the client.
    payload = {
        field["name"]: field.get("value", "")
        for field in form.select("input[type='hidden'][name]")
    }
    payload["UserName"] = username
    payload["Password"] = password
    post_url = urljoin(str(page.url), form.get("action") or "")

    response = common.submit_login(client, post_url, payload)
    response.raise_for_status()
    if is_signed_in(response.text):
        return True
    if login_failed(response.text):
        log.error("login: REJECTED by the site (wrong user name or password)")
        return False
    raise common.StructureChangedError(
        "The Tasmania login response was neither the signed-in page nor the "
        "'incorrect' message -- the login page may have changed."
    )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def _clean(text) -> str | None:
    text = " ".join((text or "").split())
    return text or None


def detail_url(site_id: str) -> str:
    return f"{BASE_URL}/OpenForBids/Details/{site_id}"


def parse_listing(html: str) -> list[dict]:
    """
    Every real tender on the list page, once each: {id, url, title, category,
    agency, opened, closes}. The "Closes:" heading row above a group of tenders
    applies to each tender under it until the next heading.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    entries, seen, closes = [], set(), None
    for row in soup.select(LIST_TABLE_SELECTOR + " tr"):
        heading = row.find("th", colspan=True)
        if heading is not None:
            text = _clean(heading.get_text(" ", strip=True)) or ""
            closes = re.sub(r"^Closes:\s*", "", text, flags=re.I) or None
            continue
        anchor = row.select_one(DETAIL_LINK_SELECTOR)
        match = DETAIL_ID_PATTERN.search(anchor.get("href") or "") if anchor else None
        if not match or match.group(1) in seen:
            continue
        title = _clean(anchor.get_text(" ", strip=True))
        if (title or "").lower().startswith(DEMO_TITLE_PREFIX):
            continue
        cells = row.find_all("td", recursive=False)
        category = None
        for item in row.select("li"):
            text = _clean(item.get_text(" ", strip=True)) or ""
            if text.lower().startswith("unspsc category:"):
                category = _clean(text.split(":", 1)[1])
        seen.add(match.group(1))
        entries.append({
            "id": match.group(1),
            "url": detail_url(match.group(1)),
            "title": title,
            "category": category,
            "agency": _clean(cells[2].get_text(" ", strip=True)) if len(cells) > 2 else None,
            "opened": _clean(cells[3].get_text(" ", strip=True)) if len(cells) > 3 else None,
            "closes": closes,
        })
    return entries


def collect_all_listing_entries(client, limit: int = 0) -> list[dict]:
    """
    Every open tender, from the one list page. `limit` of 0 means all of them.

    Raises StructureChangedError if the page has no tenders: the portal always
    has open tenders, so an empty list means the markup changed (or we were
    sent somewhere that is not the list), not that there is nothing to scrape.
    """
    response = common.request_with_retries(client, "get", LIST_URL, headers=HEADERS, timeout=60.0)
    response.raise_for_status()
    entries = parse_listing(response.text)
    if not entries:
        raise common.StructureChangedError("No tenders on the Open For Bids list page.")
    return entries[:limit] if limit else entries


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------

def _field_value(field) -> str | None:
    """
    The text of one value cell: list items (an address, an enquiries block) one
    per line, rich text paragraphs one per line. Link addresses are not kept;
    only the words on the page (an e-mail shown as the link text stays).
    """
    copy = BeautifulSoup(str(field), "html.parser")
    for tag in copy(["script", "style", "input"]):
        tag.decompose()
    for anchor in copy.find_all("a"):
        href = (anchor.get("href") or "").lower()
        if "/openforbids/" in href or "/tender/" in href:
            anchor.decompose()  # "Download Files" / "Lodge a Submission" buttons
    for anchor in copy.find_all("a"):
        anchor.unwrap()  # keep the words ("Email: x@y"), not the address behind them
    for br in copy.find_all("br"):
        br.replace_with("\n")
    for block in copy.find_all(["li", "p", "div", "tr", "h1", "h2", "h3", "h4"]):
        block.append("\n")
    lines = [_clean(line) for line in copy.get_text("").split("\n")]
    text = "\n".join(line for line in lines if line)
    return text or None


def parse_detail(html: str) -> tuple[dict, int]:
    """
    Pull a tender's own fields off its detail page.

    Returns (fields, status_code). `fields` maps each label to its text, in page
    order, plus `reference` (the Unique Tender ID) and `title`. If the title or
    the tender ID is not found on an otherwise real page, returns
    ({}, SITE_STRUCTURE_CHANGE) rather than guessing at a different layout.
    A label that appears twice with the same text (Location, Enquiries) is kept once.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    labelled: dict[str, str] = {}
    for row in soup.select(FIELD_ROW_SELECTOR):
        label = row.select_one("div.editor-label")
        field = row.select_one("div.editor-field")
        name = _clean(label.get_text(" ", strip=True)) if label else None
        if not name or field is None or name in SKIPPED_LABELS:
            continue
        value = _field_value(field)
        if not value:
            continue
        if name in labelled:
            if value in labelled[name].split("\n---\n"):
                continue
            labelled[name] += "\n---\n" + value
        else:
            labelled[name] = value

    # "Documents are available from" / "Applications must be lodged at" repeat the
    # Enquiries block or hold only a link; drop the connector words and the repeats.
    for name in list(labelled):
        value = "\n".join(line for line in labelled[name].split("\n")
                          if line.lower() not in CONNECTOR_LINES)
        others = [v for n, v in labelled.items() if n != name]
        if not value or (name in REPEATING_LABELS and value in others):
            del labelled[name]
        else:
            labelled[name] = value

    reference = labelled.get(REFERENCE_LABEL)
    title = labelled.get(TITLE_LABEL)
    if not reference or not title:
        return {}, common.SITE_STRUCTURE_CHANGE
    return {"reference": reference.split("\n")[0], "title": title.split("\n")[0], "labelled": labelled}, common.SITE_SUCCESS


def format_detail_text(fields: dict, entry: dict | None = None) -> str:
    """Render the parsed fields as the tender's page-text file."""
    entry = entry or {}
    lines = [
        "=" * 80,
        f"TASMANIA ETENDERING: {fields.get('title') or fields.get('reference')}",
        "=" * 80,
        "",
    ]
    for label, value in (("Closes", entry.get("closes")), ("Opened", entry.get("opened"))):
        if value:
            lines.append(f"{label}: {value}")
    labelled = fields.get("labelled", {})
    long_labels = []
    for label, value in labelled.items():
        if "\n" in value:
            long_labels.append((label, value))
        else:
            lines.append(f"{label}: {value}")
    for label, value in long_labels:
        lines += ["", f"{label.upper()}:", "-" * 40, value]
    lines += ["", "=" * 80, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def documents_url(site_id: str) -> str:
    return f"{BASE_URL}/OpenForBids/Downloadables/{site_id}"


def parse_documents(html: str, page_url: str = BASE_URL) -> list[dict]:
    """Every file on the Downloadables page: {name, size, url}, in page order."""
    soup = BeautifulSoup(html or "", "html.parser")
    documents = []
    for anchor in soup.select(DOCUMENT_LINK_SELECTOR):
        name = _clean(anchor.get_text(" ", strip=True))
        if not name:
            continue
        size = anchor.find_next_sibling("label")
        documents.append({
            "name": name,
            "size": _clean(size.get_text(" ", strip=True)).strip("()") if size else None,
            "url": urljoin(page_url, anchor["href"]),
        })
    return documents


def download_documents(client, site_id: str, folder: str) -> tuple[list[dict], int, int]:
    """
    Fetch every file the tender lists, one by one.

    Returns (attachments, advertised, failed). Raises SessionLapsedError when
    the site shows its sign-in form in place of the page or a file, and lets an
    HTTP error on the documents page through for the caller to judge. A file
    that fails on its own is counted in `failed` and does not stop the others.
    """
    page_url = documents_url(site_id)
    page = common.request_with_retries(client, "get", page_url, headers=HEADERS, timeout=30.0)
    page.raise_for_status()
    if find_login_form(page.text) is not None:
        raise SessionLapsedError("the documents page asked for a sign-in")
    documents = parse_documents(page.text, str(page.url))

    attachments, failed = [], 0
    for document in documents:
        time.sleep(PAUSE_SECONDS)
        try:
            attachment, extracted = common.download_attachment(
                client, document["url"], folder, common.sanitise_filename(document["name"]),
                headers=dict(HEADERS, Referer=page_url))
            attachments.append(attachment)
            if not extracted:
                failed += 1
        except common.DocumentGatedError:
            raise SessionLapsedError(f"{document['name']} came back as a web page, not a file")
        except common.EncryptedFileError:
            failed += 1  # password protected: dropped, not an attachment
        except httpx.HTTPStatusError as exc:
            if blocked_code(exc) is not None:
                raise
            log.warning("       download FAILED: %s: %s", document["name"], exc)
            run_notes.note_reason(run_notes.DOWNLOAD, document["name"])
            failed += 1
        except Exception as exc:
            log.warning("       download FAILED: %s: %s", document["name"], exc)
            run_notes.note_reason(run_notes.DOWNLOAD, document["name"])
            failed += 1
    return attachments, len(documents), failed


# ---------------------------------------------------------------------------
# Per-tender and full-run orchestration
# ---------------------------------------------------------------------------

def scrape_opportunity(client, entry: dict, output_dir: str = "tenders_data",
                       signed_in: bool = False, taken=None) -> tuple[int, dict]:
    """
    Scrape one tender into its own folder.

    Returns (status_code, tender). `tender` is empty if the page could not be
    parsed. The page text is saved before any download, so a failed download
    never costs the tender its text. `taken` is the folder names already
    used this run: a Unique Tender ID another agency has also used gets the
    site's own id added so the two never share a folder (`taken` maps folder name -> site id).

    Raises SessionLapsedError when the site wanted a sign-in and we had signed in.
    """
    url = entry["url"]
    response = common.request_with_retries(client, "get", url, headers=HEADERS, timeout=30.0)
    response.raise_for_status()
    fields, code = parse_detail(response.text)
    if code != common.SITE_SUCCESS:
        return code, {}

    reference = fields["reference"]
    if taken is not None:
        if taken.get(reference, entry["id"]) != entry["id"]:
            reference = f"{reference} ({entry['id']})"
        taken[reference] = entry["id"]
    folder = common.tender_dir(reference, output_dir)
    common.save_page_text(folder, reference, format_detail_text(fields, entry))
    common.append_full_page_text(folder, reference, response.text, PAGE_CONTENT_SELECTOR)
    common.add_source_url(folder, reference, url)

    tender = {
        "tender_id": reference,
        "title": fields.get("title"),
        "folder": folder,
        "attachments": [],
        "source_url": url,
        "documents_gated": False,
    }

    if not signed_in:
        # The documents page needs an account. Say what is lost, once per tender.
        listed = detail_has_downloads(response.text)
        if listed:
            tender["documents_gated"] = True
            log.warning("       documents: not signed in -- skipped")
            run_notes.note_reason(run_notes.PORTAL)
            return common.TENDER_PARTIAL, tender
        reporting.documents_line(log, 0, 0)
        return common.SITE_SUCCESS, tender

    try:
        attachments, advertised, failed = download_documents(client, entry["id"], folder)
    except SessionLapsedError:
        tender["documents_gated"] = True
        raise
    tender["attachments"] = attachments
    reporting.documents_line(log, len(attachments), advertised)
    missing = advertised - len(attachments)
    if missing > 0:
        run_notes.note_reason(run_notes.DOWNLOAD, f"{missing} document(s) missing")
    return (common.TENDER_PARTIAL if (failed or missing > 0) else common.SITE_SUCCESS), tender


def detail_has_downloads(html: str) -> bool:
    """True if the detail page offers the "Download Files" link."""
    return BeautifulSoup(html or "", "html.parser").select_one(
        "a[href*='/OpenForBids/Downloadables/']") is not None


def _scrape_site(limit: int = 0, output_dir: str = "tenders_data") -> tuple[int, list[dict]]:
    """
    Scrape every open Tasmanian tender. `limit` of 0 means every tender found.

    Returns (site_code, tenders). A failed login with credentials set is
    SITE_LOGIN_FAILED, but the page text is still scraped. A 429 or 403 stops
    the run at once.
    """
    username, password = credentials()
    # Say plainly whether the secrets reached this process. Values are never logged.
    log.info("login: TAS_USERNAME %s, TAS_PASSWORD %s",
             "set" if username else "MISSING", "set" if password else "MISSING")
    tenders: list[dict] = []
    tender_codes: set[int] = set()
    signed_in = False
    try:
        with httpx.Client(follow_redirects=True) as client:
            if username and password:
                signed_in = login(client)
                if signed_in:
                    log.info("login: ok")
                else:
                    tender_codes.add(common.SITE_LOGIN_FAILED)
                    log.error("login: FAILED -- continuing without documents")
            else:
                log.warning("login: no credentials -- documents will be skipped and "
                            "tenders with documents will be TENDER_PARTIAL")

            entries = collect_all_listing_entries(client, limit)
            log.info("listing: %d tender(s) found", len(entries))
            run_notes.note_total(len(entries))

            all_codes = []
            taken: dict[str, str] = {}
            relogins = 0
            for index, entry in enumerate(entries, start=1):
                try:
                    try:
                        code, tender = scrape_opportunity(client, entry, output_dir, signed_in, taken)
                    except SessionLapsedError as exc:
                        if relogins >= MAX_RELOGINS:
                            raise
                        relogins += 1
                        log.warning("       signed out (%s) -- signing in again (%d/%d) and "
                                    "retrying this tender", exc, relogins, MAX_RELOGINS)
                        signed_in = login(client)
                        if not signed_in:
                            log.error("login: FAILED on re-login -- continuing without documents")
                            tender_codes.add(common.SITE_LOGIN_FAILED)
                        code, tender = scrape_opportunity(client, entry, output_dir, signed_in, taken)
                    if tender:
                        tenders.append(tender)
                    tender_codes.add(code)
                    all_codes.append(code)
                    reporting.tender_line(log, index, len(entries),
                                          tender.get("tender_id") or entry["url"], code)
                except httpx.HTTPStatusError as exc:
                    blocked = blocked_code(exc)
                    if blocked is not None:
                        log.error("HTTP %s from %s -- stopping the run",
                                  exc.response.status_code, exc.request.url)
                        return blocked, tenders
                    reporting.tender_failed(log, index, len(entries), entry["url"], exc)
                    tender_codes.add(common.TENDER_PARTIAL)
                    all_codes.append(common.TENDER_PARTIAL)
                except Exception as exc:
                    reporting.tender_failed(log, index, len(entries), entry["url"], exc)
                    tender_codes.add(common.TENDER_PARTIAL)
                    all_codes.append(common.TENDER_PARTIAL)
                time.sleep(PAUSE_SECONDS)

            reporting.diagnose(log, all_codes, logged_in=signed_in)

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