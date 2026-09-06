"""
Publishing scraped tender folders to Cloud Storage.

The local directory is always the primary output -- the scrapers write the
agreed one-directory-per-tender tree either way, and nothing downstream has to
know about this module. Mirroring to Cloud Storage is purely additive and is
off unless OUTPUT_BUCKET is set, so a local run and Rad's existing pipeline
behave exactly as they do today, and the deploy is reversible by unsetting one
environment variable rather than by rolling back an image.

When it is set, each tender folder is mirrored to

    gs://<bucket>/<prefix>/<source_id>/<TENDER_REF>/...

The source_id segment (read from the folder's own tender.json) keeps raw
scrape output namespaced away from whatever prefix the front end will build
document URLs from -- the bucket is shared, so raw .txt must not land in it.

The bucket is NOT made public: serving these files to the UI is the API's job,
via signed URLs, the same way the React app only ever talks to the backend.
"""

import json
import logging
import mimetypes
import os
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PREFIX = "raw"
UNKNOWN_SOURCE = "unknown-source"


def bucket_name():
    """The destination bucket, or None when publishing is switched off."""
    return os.environ.get("OUTPUT_BUCKET") or None


def folder_source_id(folder):
    """
    Which portal this tender came from, read from its own tender.json.

    Falls back to a placeholder rather than raising: a folder with an
    unreadable manifest should still be uploaded somewhere findable, not
    dropped.
    """
    manifest = Path(folder) / "tender.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))["source_id"] or UNKNOWN_SOURCE
    except Exception:
        log.warning("no readable source_id in %s -- filing under %s", manifest, UNKNOWN_SOURCE)
        return UNKNOWN_SOURCE


def blob_prefix(folder, prefix=DEFAULT_PREFIX):
    """The object-name prefix for one tender: <prefix>/<source_id>/<REF>."""
    return f"{prefix}/{folder_source_id(folder)}/{Path(folder).name}"


def upload_tender_folder(bucket, folder, prefix=DEFAULT_PREFIX):
    """
    Upload one tender folder, returning the number of objects written.

    Import is deferred so the scrapers run without google-cloud-storage
    installed, which keeps the local development path dependency-light.
    """
    from google.cloud import storage

    folder = Path(folder)
    client = storage.Client()
    target = client.bucket(bucket)
    base = blob_prefix(folder, prefix)

    written = 0
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        blob = target.blob(f"{base}/{path.relative_to(folder)}")
        content_type, _ = mimetypes.guess_type(path.name)
        blob.upload_from_filename(str(path), content_type=content_type)
        written += 1

    log.info("uploaded %s (%d object(s)) to gs://%s/%s", folder.name, written, bucket, base)
    return written


def publish(output_dir, prefix=DEFAULT_PREFIX):
    """
    Mirror every tender folder in `output_dir` to the configured bucket.

    Returns (folders, objects). A folder that fails to upload is logged and
    skipped rather than aborting the run -- losing one tender is better than
    losing the whole scrape.
    """
    bucket = bucket_name()
    if not bucket:
        log.info("OUTPUT_BUCKET not set -- leaving results in %s", output_dir)
        return 0, 0

    folders = objects = 0
    for folder in sorted(Path(output_dir).iterdir()):
        if not folder.is_dir():
            continue
        try:
            objects += upload_tender_folder(bucket, folder, prefix)
            folders += 1
        except Exception as exc:
            log.error("failed to upload %s: %s", folder.name, exc)

    log.info("published %d tender folder(s), %d object(s)", folders, objects)
    return folders, objects
