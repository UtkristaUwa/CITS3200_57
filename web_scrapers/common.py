"""
Shared helpers for every tender scraper.

Output format (the standard all scrapers now write):

    tenders_data/
        <TENDER_REF>/
            <TENDER_REF>.txt    the text scraped from the tender's own page
            tender.json         the tender in ingestion's 20-field shape
            <attachment files>  the documents that were actually downloadable

One directory per tender -- never one file per website.

tender.json matches ingestion/sample_tender.json and validates against
ingestion/tender.schema.json, so a scrape feeds straight into
ingestion/validate_and_submit.py. It is written even when nothing could be
downloaded: its raw_extra.scrape block is how a consumer tells "this tender has
no attachments" from "the attachments are behind a login we do not have".
"""

import json
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

# Repo-root tenders_data/ -- this file lives at <repo>/web_scrapers/common.py.
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "tenders_data"

RECORD_NAME = "tender.json"

# Windows-illegal characters plus anything that would let a scraped filename
# escape its tender folder.
_UNSAFE_FILENAME = re.compile(r'[\\/*?:"<>|\x00-\x1f]')
_UNSAFE_REF = re.compile(r"[^A-Za-z0-9._-]+")

# Some portals hand back a generic name; keep the extension we can infer.
FALLBACK_DOCUMENT_NAME = "document"


@dataclass
class Document:
    """One attachment advertised on a tender page."""

    file_name: str
    url: Optional[str] = None
    version: Optional[str] = None
    size_label: Optional[str] = None       # as printed on the page, e.g. "9 MB"
    downloaded: bool = False
    local_path: Optional[str] = None       # relative to the tender folder
    bytes_written: Optional[int] = None
    error: Optional[str] = None            # why the download did not happen


def sanitise_reference(reference, fallback="tender"):
    """Turn a tender reference into a safe folder name ('A26/6' -> 'A26_6')."""
    safe = _UNSAFE_REF.sub("_", (reference or "").strip()).strip("._-")
    return safe or fallback


def sanitise_filename(name):
    """
    Make a scraped filename safe to write inside a tender folder.

    Strips any directory component so a crafted `../../etc/passwd` name from a
    page cannot write outside the folder, replaces illegal characters, and
    trims to a length every filesystem accepts.
    """
    name = unquote(name or "").strip()
    name = name.replace("\\", "/").split("/")[-1]  # drop directories
    name = _UNSAFE_FILENAME.sub("_", name).strip(" .")
    if not name or name in (".", ".."):
        return FALLBACK_DOCUMENT_NAME
    if len(name) > 180:
        stem, dot, suffix = name.rpartition(".")
        name = (stem[:170] + dot + suffix[:9]) if dot else name[:180]
    return name


def tender_dir(reference, output_dir=None, fallback="tender"):
    """Create (if needed) and return this tender's own directory."""
    base = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    folder = base / sanitise_reference(reference, fallback)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_tender_text(folder, text):
    """
    Write the page text as <REF>.txt inside the tender's folder.

    The file is always named after the folder, which is what the document
    scraper downstream looks for when it appends attachment text.
    """
    folder = Path(folder)
    out_file = folder / f"{folder.name}.txt"
    out_file.write_text(text, encoding="utf-8")
    return out_file


def unique_path(folder, file_name):
    """
    Return a path inside `folder` that does not already exist.

    Portals happily list two attachments with the same name (different
    versions); silently overwriting one with the other loses data, so the
    second becomes 'name (2).pdf'.
    """
    folder = Path(folder)
    candidate = folder / file_name
    if not candidate.exists():
        return candidate
    stem, dot, suffix = file_name.rpartition(".")
    if not dot:
        stem, suffix = file_name, ""
    for counter in range(2, 1000):
        alt = f"{stem} ({counter}){dot}{suffix}"
        candidate = folder / alt
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not find a free filename for {file_name!r}")


def filename_from_response(response, fallback_name):
    """
    Work out what to call a downloaded file.

    Prefers the server's Content-Disposition filename, falls back to the name
    scraped off the page, and appends an extension inferred from the
    Content-Type when the name has none (some portals serve 'document.bin').
    """
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disposition, re.I)
    name = sanitise_filename(match.group(1)) if match else sanitise_filename(fallback_name)

    if "." not in name:
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        extension = mimetypes.guess_extension(content_type) if content_type else None
        if extension and extension != ".bin":
            name += extension
    return name


def download_document(client, document, folder, timeout=60.0, headers=None):
    """
    Stream one document into the tender's folder, recording the outcome on it.

    Never raises: a single unreachable attachment must not abort the tender (or
    the run). The failure is recorded on the Document so it lands in the
    manifest and can be retried or reported later.
    """
    folder = Path(folder)
    if not document.url:
        document.error = document.error or "no download URL on the page"
        return document

    try:
        with client.stream(
            "GET", document.url, timeout=timeout, headers=headers or {}
        ) as response:
            if response.status_code != 200:
                document.error = f"HTTP {response.status_code}"
                return document

            # A login redirect returns 200 with an HTML page, not the file.
            content_type = (response.headers.get("content-type") or "").lower()
            expects_html = document.file_name.lower().endswith((".htm", ".html"))
            if "text/html" in content_type and not expects_html:
                document.error = "login required (server returned an HTML page)"
                return document

            target = unique_path(folder, filename_from_response(response, document.file_name))
            written = 0
            with open(target, "wb") as handle:
                for chunk in response.iter_bytes(chunk_size=8192):
                    handle.write(chunk)
                    written += len(chunk)

        if written == 0:
            target.unlink(missing_ok=True)
            document.error = "server returned an empty file"
            return document

        document.downloaded = True
        document.local_path = target.name
        document.bytes_written = written
        document.error = None
    except Exception as exc:  # network error, disk error, malformed response
        document.error = f"{exc.__class__.__name__}: {exc}"
    return document


def write_tender_record(folder, record):
    """
    Write tender.json -- the tender in ingestion's 20-field shape.

    Returns the path written. The record is produced by
    tender_record.build_record; nothing here reshapes it, so what lands on disk
    is exactly what ingestion/validate_and_submit.py expects to read.
    """
    folder = Path(folder)
    out_file = folder / RECORD_NAME
    out_file.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_file


# ---------------------------------------------------------------------------
# Browser setup for the two portals that need one (VIC, QLD).
# ---------------------------------------------------------------------------

# Chrome's default shared-memory segment is 64MB in a container, which is not
# enough to render a page -- it crashes with "session deleted because of page
# crash". Writing shm to /tmp instead is the standard container workaround.
CONTAINER_CHROME_ARGS = "disable-dev-shm-usage,disable-gpu"


def in_container():
    """
    True when we are running inside the scraper image.

    The Dockerfile sets this explicitly rather than sniffing for /.dockerenv or
    cgroup paths, which differ between Docker, Cloud Run and Kubernetes.
    """
    return os.environ.get("RUNNING_IN_CONTAINER", "").lower() in ("1", "true", "yes")


def has_display():
    """True when an X display (real or Xvfb) is available to Chrome."""
    return bool(os.environ.get("DISPLAY"))


def effective_headless(headless=True):
    """Whether Chrome will really run headless, after the display override."""
    return False if has_display() else headless


def describe_browser_mode(headless=True):
    """Human-readable description of how Chrome is about to be launched."""
    if has_display():
        return f"headed on virtual display {os.environ.get('DISPLAY')}"
    return "headless" if headless else "visible window"


def build_uc_driver(headless=True):
    """
    Create the SeleniumBase UC-mode Chrome driver both browser scrapers use.

    UC mode strips the automation fingerprints Cloudflare looks for. Two
    container-specific adjustments matter here:

      * Headless Chrome is markedly easier for Cloudflare to spot than a real
        window. In the image we therefore run Chrome *headed* against an Xvfb
        virtual display, so `headless` is ignored whenever DISPLAY is set.
      * Chrome cannot run as root without --no-sandbox, and needs its shared
        memory moved off the container's tiny /dev/shm.

    Outside a container this behaves exactly as before, so local runs are
    unchanged.
    """
    from seleniumbase import Driver

    options = {"uc": True, "headless": effective_headless(headless)}

    if in_container():
        options["no_sandbox"] = True
        options["chromium_arg"] = CONTAINER_CHROME_ARGS

    return Driver(**options)
