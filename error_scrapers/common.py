#File holding our helper functions that are used across all scrapers. Also includes the status error codes for failures.

import os
import re
import fitz
import docx
import openpyxl
from docx.document import Document as _DocxDocument
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
import httpx

#Error status codes for failures
#Scraping was a total success
SITE_SUCCESS = 0
#The URL provided couldnt be reached
SITE_TOTAL_FAILURE = 1
#The site login failed
SITE_LOGIN_FAILED = 2
#Anti bot detections have blocked us
SITE_BOT_BLOCKED = 3
#The structure of the website has changed thus causing our scraper to fail
SITE_STRUCTURE_CHANGE = 4
#Site has temporarily blocked our access
SITE_RATE_LIMITED = 5
#A tender only has partial information gathered and needs to be verified
TENDER_PARTIAL = 6

#-----
#This is our folder and filename handling for the tenders
#-----

#This function is specifically for returning the folder path for a tender, and creating one if there isn't one.
#All of a tenders output, the page html, attachments, and extracted attachement text lives here and is named after the tender ID
def tender_dir(tender_id: str, base_dir: str = "tenders_data") -> str:
    path = os.path.join(base_dir, sanitise_filename(tender_id))
    os.makedirs(path, exist_ok=True)
    return path

#This function strips anything that could escape the tender folder or break the filesystem as we cant trust a name scraped off a page
def sanitise_filename(name: str) -> str:
    name = name.replace("/", "_").replace("\\", "_")
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip()

#-----
#This is the section that saves the per tender output
#-----

#This function writes the scraped page content html content
def save_page_text(folder: str, tender_id: str, text: str) -> None:
    # Prefixed with __tender__ so the AI processor and attachment store can
    # distinguish this pipeline-generated file from real downloaded attachments.
    path = os.path.join(folder, f"__tender__{sanitise_filename(tender_id)}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

#This function makes sure we never silently overwrite a file. Portals often attach
#several files with the same name, so the second one would otherwise replace the first.
def unique_path(folder: str, filename: str) -> str:
    base, extension = os.path.splitext(sanitise_filename(filename))
    candidate = os.path.join(folder, f"{base}{extension}")
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{base} ({counter}){extension}")
        counter += 1
    return candidate

#This function focusses on the downloaded attachment from the tender page
def save_attachment(folder: str, filename: str, content: bytes) -> str:
    path = unique_path(folder, filename)
    with open(path, "wb") as f:
        f.write(content)
    return path

#Same as save_attachment, but writes an httpx streaming response straight to disk in
#chunks. Tender packs can include video, and the pipeline runs on Cloud Run where the
#filesystem is memory -- holding a whole file in RAM can take the run out.
def save_attachment_stream(folder: str, filename: str, response) -> str:
    path = unique_path(folder, filename)
    with open(path, "wb") as f:
        for chunk in response.iter_bytes():
            f.write(chunk)
    return path

#This function is for extracted the attachment file's contents, we also make sure the file names match between the attachement and extracted information
def save_extracted_text(folder: str, attachment_filename: str, text: str) -> str:
    path = os.path.join(folder, f"{sanitise_filename(attachment_filename)}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path

#-----
#These are the helper functions that help with the attachement information extraction
#-----

class StructureChangedError(Exception):
    """Raised when a page's response matches neither a known success or a known failure pattern."""

#This class is raised when the attachement cant be read for text
class ExtractionError(Exception):
    """Raised when a PDF/DOCX file can't be read for text."""

#This is our pdf extraction code
def extract_pdf(file_path: str) -> str:
    try:
        text = ""
        with fitz.open(file_path) as pdf_doc:
            for page in pdf_doc:
                text += page.get_text("text") + "\n"
        return text
    except Exception as e:
        raise ExtractionError(f"PDF extraction failed on {file_path}: {e}") from e

def _iter_block_items(parent):
    """
    Yield each paragraph and table in a docx document (or table cell),
    in the order they actually appear -- not paragraphs-then-tables,
    but interleaved as a person reading top to bottom would encounter
    them. Walks the underlying XML body directly, since python-docx's
    own doc.paragraphs and doc.tables are two separate, unordered
    collections with no way to tell which came first.
    """
    if isinstance(parent, _DocxDocument):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise ValueError("_iter_block_items: unsupported parent type")
 
    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)
 
 
def _element_text(element):
    """
    Every visible text node inside an XML element, regardless of
    whether it sits in a plain run, a hyperlink, a content control /
    dropdown (w:sdt), or a floating textbox / watermark (w:drawing or
    the legacy w:pict). Scanning all descendant w:t nodes is
    structure-agnostic, unlike the higher-level .text properties
    (paragraph.text, cell.text), which only walk certain expected
    shapes and miss anything nested one level deeper than that.
    """
    return "".join(t.text or "" for t in element.iter(qn("w:t")))
 
 
def _table_to_text(table):
    """Render one table's cells as readable, tab-separated rows."""
    lines = []
    for row in table.rows:
        cells = [_element_text(cell._tc) for cell in row.cells]
        if any(cell.strip() for cell in cells):  # skip fully-empty rows
            lines.append("\t".join(cells))
    return "\n".join(lines)
 
 
def _all_header_footer_elements(document):
    """
    Every header/footer XML part actually referenced by the document,
    including 'even page' headers/footers -- which python-docx's public
    API does not expose at all. Reads each section's raw
    headerReference/footerReference elements and resolves them through
    the document part's own relationships, rather than trusting
    section.header/.footer/.first_page_header/.first_page_footer, which
    can silently return the wrong (or a newly-created, empty) part when
    a document uses header/footer types those properties don't know
    about.
    """
    doc_part = document.part
    seen_rids, elements = set(), []
    for section in document.sections:
        sect_pr = section._sectPr
        for tag in (qn("w:headerReference"), qn("w:footerReference")):
            for ref in sect_pr.findall(tag):
                r_id = ref.get(qn("r:id"))
                if r_id and r_id not in seen_rids and r_id in doc_part.rels:
                    seen_rids.add(r_id)
                    elements.append(doc_part.rels[r_id].target_part.element)
    return elements
 
 
def extract_docx(file_path: str) -> str:
    """
    Extract all readable text from a Word document: headers, footers
    (including even-page variants and watermarks), body paragraphs,
    tables, and content-control/dropdown text -- everything a person
    would actually see when reading the document, not just its main
    paragraph flow.
    """
    try:
        doc = docx.Document(file_path)
        parts = []
 
        for element in _all_header_footer_elements(doc):
            text = _element_text(element)
            if text.strip():
                parts.append(text)
 
        for block in _iter_block_items(doc):
            if isinstance(block, Paragraph):
                text = _element_text(block._p)
            elif isinstance(block, Table):
                text = _table_to_text(block)
            else:
                continue
            if text.strip():
                parts.append(text)
 
        return "\n".join(part for part in parts if part.strip())
    except Exception as e:
        raise ExtractionError(f"DOCX extraction failed on {file_path}: {e}") from e

def extract_xlsx(file_path: str) -> str:
    """
    Extract all cell content from every sheet in a spreadsheet.
 
    data_only=True returns each formula cell's last-calculated value
    rather than the formula text itself -- what a person looking at the
    spreadsheet would actually see. read_only=True keeps memory use low
    on large files.
    """
    try:
        wb = load_workbook(file_path, data_only=True, read_only=True)
        parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            sheet_lines = [f"## Sheet: {sheet_name}"]
            for row in ws.iter_rows(values_only=True):
                cells = ["" if cell is None else str(cell) for cell in row]
                if any(cell.strip() for cell in cells):  # skip fully-empty rows
                    sheet_lines.append("\t".join(cells))
            if len(sheet_lines) > 1:  # only keep sheets that had real content
                parts.append("\n".join(sheet_lines))
        return "\n\n".join(parts)
    except Exception as e:
        raise ExtractionError(f"XLSX extraction failed on {file_path}: {e}") from e

#The file types we can pull text out of. Anything else (csv, images, video) is still
#saved as an attachment, it just has no extracted .txt next to it.
EXTRACTORS = {
    ".pdf": extract_pdf,
    ".docx": extract_docx,
    ".xlsx": extract_xlsx,
}

def extract_attachment_text(folder: str, file_name: str) -> bool:
    """
    Write <file_name>.txt next to a saved attachment, when we have an extractor
    for its type. Returns False only when extraction of a supported type
    failed -- an unsupported type is not a failure, there's just no text in it.
    """
    extractor = EXTRACTORS.get(os.path.splitext(file_name)[1].lower())
    if extractor is None:
        return True
    try:
        save_extracted_text(folder, file_name, extractor(os.path.join(folder, file_name)))
        return True
    except ExtractionError as e:
        print(f"EXTRACTION FAILED: {e}")
        return False

#Raised when a portal answers a document request with an HTML page (almost
#always its login form) instead of the file.
class DocumentGatedError(Exception):
    """Raised when a document download comes back as a web page, not a file."""

def download_attachment(client, url: str, folder: str, file_name: str,
                        headers: dict | None = None, timeout: float = 60.0) -> tuple[dict, bool]:
    """
    Stream one attachment into the tender folder and extract its text.

    Returns (attachment, extracted). `attachment` is the manifest entry the
    pipeline needs ({file_name, content_type, size_bytes}), using the name
    actually written -- save_attachment_stream may have de-duplicated it.
    `extracted` is False when text extraction failed on a supported type, so
    the caller can report the tender as partial.

    Raises on a failed download (HTTP error, network error, or a login page
    served in place of the file), so the caller decides what that means.
    """
    with client.stream("GET", url, headers=headers or {}, timeout=timeout) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type")
        wants_html = file_name.lower().endswith((".htm", ".html"))
        if content_type and "text/html" in content_type.lower() and not wants_html:
            raise DocumentGatedError(f"{url} returned a web page, not a file")
        raw_path = save_attachment_stream(folder, file_name, response)

    saved_name = os.path.basename(raw_path)
    if os.path.getsize(raw_path) == 0:
        os.remove(raw_path)
        raise ExtractionError(f"{url} returned an empty file")

    attachment = {
        "file_name": saved_name,
        "content_type": content_type,
        "size_bytes": os.path.getsize(raw_path),
    }
    return attachment, extract_attachment_text(folder, saved_name)

MAX_ZIP_DEPTH = 5

def unpack_zip(zip_file, folder: str, depth: int = 0) -> tuple[list[dict], bool]:
    """
    Unpack a downloaded document bundle into the tender folder, extracting
    text from each file. `zip_file` is a path or a file-like object.

    Nested zips (agencies bundle response templates this way) are recursed
    into rather than saved as an unreadable blob. Member names are reduced to
    a bare filename, so an archive holding ../../evil.txt lands inside the
    tender folder or not at all.

    Returns (attachments, any_failed).
    """
    import io
    import zipfile

    attachments, any_failed = [], False
    if depth > MAX_ZIP_DEPTH:
        return attachments, True

    with zipfile.ZipFile(zip_file) as zf:
        for member in zf.infolist():
            file_name = os.path.basename(member.filename.replace("\\", "/"))
            if member.is_dir() or not file_name:
                continue
            if file_name.lower().endswith(".zip"):
                nested, nested_failed = unpack_zip(io.BytesIO(zf.read(member)), folder, depth + 1)
                attachments += nested
                any_failed = any_failed or nested_failed
                continue
            with zf.open(member) as source:
                path = unique_path(folder, file_name)
                with open(path, "wb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
            saved_name = os.path.basename(path)
            attachments.append({
                "file_name": saved_name,
                "content_type": None,
                "size_bytes": os.path.getsize(path),
            })
            any_failed = any_failed or not extract_attachment_text(folder, saved_name)

    return attachments, any_failed

#The site-level code for a run, from the per-tender codes it collected. Same
#precedence every scraper uses: a structure change outranks a partial tender,
#since it means the scraper itself needs fixing.
def site_code_from(tender_codes) -> int:
    codes = set(tender_codes) - {SITE_SUCCESS}
    if not codes:
        return SITE_SUCCESS
    for code in (SITE_BOT_BLOCKED, SITE_STRUCTURE_CHANGE, SITE_LOGIN_FAILED, SITE_RATE_LIMITED):
        if code in codes:
            return code
    return TENDER_PARTIAL

#-----
#This is our helper function to log in to sites
#-----

def submit_login(client: httpx.Client, url: str, payload: dict) -> httpx.Response:
    return client.post(
        url,
        data=payload,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        follow_redirects=True,
        timeout=25.0,
    )