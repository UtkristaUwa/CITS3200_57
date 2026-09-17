"""
attachment_store.py

Copies a tender's original attachments out of the pipeline's temporary
working directory and into Cloud Storage, before that directory (and
everything in it) is deleted at the end of the run.

Why this exists: the pipeline runs as a Cloud Run job, where the
filesystem is memory. Every file the scrapers download lives only for the
duration of the run, so anything we want to keep has to be copied out
while it is still there. The bucket is the permanent home; BigQuery keeps
a pointer to it.

Returns one record per file, shaped for the `documents` column in
BigQuery, so the caller can hand them straight to the database write.
"""

import hashlib
import logging
import mimetypes
import os
from datetime import datetime, timezone

logger = logging.getLogger("AttachmentStore")

# The bucket lives in australia-southeast1, alongside BigQuery and the job.
BUCKET_NAME = os.environ.get("ATTACHMENTS_BUCKET", "tenderai-dev-documents")

# Anything larger than this is skipped rather than risking the job's memory
# limit. Raise it once the job has the headroom.
MAX_ATTACHMENT_MB = int(os.environ.get("MAX_ATTACHMENT_MB", "500"))

# Lets a developer run the pipeline locally without GCP credentials.
UPLOADS_ENABLED = os.environ.get("ATTACHMENT_UPLOADS", "on").lower() not in {
    "off", "false", "0", "no",
}

# Local copies are deleted after upload so only one file is ever in memory.
# Set to keep them when debugging a run by hand.
KEEP_LOCAL_COPIES = os.environ.get("KEEP_LOCAL_COPIES", "off").lower() in {
    "on", "true", "1", "yes",
}

# The file_type values the BigQuery schema allows.
_FILE_TYPES = {".pdf": "pdf", ".docx": "docx", ".rtf": "rtf"}


def get_bucket():
    """
    The bucket handle.

    Deliberately `client.bucket()` and not `client.get_bucket()`: the
    former is a local object with no API call, the latter calls
    buckets.get, which the pipeline's service account does not have
    permission for. It can read and write objects, not read bucket
    metadata. The two lines look interchangeable and are not.
    """
    from google.cloud import storage  # imported late so local runs don't need it

    return storage.Client().bucket(BUCKET_NAME)


def sha256_of(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without reading it into memory in one go."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_path(source_id: str, tender_ref: str, digest: str, file_name: str) -> str:
    """
    Where a file lives in the bucket.

    The hash prefix means re-scraping an unchanged attachment produces the
    same path, so it is skipped rather than stored twice, and two tenders
    attaching an identical document only pay for it once.
    """
    return f"tenders/{source_id}/{tender_ref}/{digest[:16]}-{file_name}"


def file_type_of(file_name: str) -> str:
    """The schema's coarse file_type. Real detail lives in content_type."""
    return _FILE_TYPES.get(os.path.splitext(file_name)[1].lower(), "other")


def upload_attachment(local_path, source_id, tender_ref, bucket=None, content_type=None):
    """
    Copy one file to the bucket and describe it.

    Returns a record ready for the `documents` column, or None if the file
    was skipped. Never raises: one unreadable attachment must not take
    down a run that has already scraped hundreds of tenders.
    """
    file_name = os.path.basename(local_path)

    try:
        size_bytes = os.path.getsize(local_path)
    except OSError as e:
        logger.warning(f"Cannot read {file_name}: {e}")
        return None

    if size_bytes > MAX_ATTACHMENT_MB * 1024 * 1024:
        logger.warning(
            f"Skipping {file_name}: {size_bytes / 1024 / 1024:.0f}MB exceeds the "
            f"{MAX_ATTACHMENT_MB}MB limit"
        )
        return None

    if content_type is None:
        content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    try:
        digest = sha256_of(local_path)
        path = object_path(source_id, tender_ref, digest, file_name)

        if UPLOADS_ENABLED:
            from google.api_core import exceptions as api_exceptions

            blob = (bucket or get_bucket()).blob(path)
            # Gives the download button the original filename instead of the
            # hashed object name.
            blob.content_disposition = f'attachment; filename="{file_name}"'
            blob.metadata = {
                "source_id": source_id,
                "tender_ref": tender_ref,
                "original_file_name": file_name,
            }
            try:
                # if_generation_match=0 means "only if it doesn't exist yet",
                # so a re-scrape costs one rejected request instead of an
                # upload, and never overwrites what is already stored.
                blob.upload_from_filename(
                    local_path, content_type=content_type, if_generation_match=0
                )
            except api_exceptions.PreconditionFailed:
                logger.info(f"Already stored, skipping upload: {file_name}")
    except Exception as e:
        logger.error(f"Upload failed for {file_name}: {e}")
        return None

    return {
        "file_name": file_name,
        "file_type": file_type_of(file_name),
        "content_type": content_type,
        "size_bytes": size_bytes,
        "checksum_sha256": digest,
        "storage_uri": f"gs://{BUCKET_NAME}/{path}",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }


def attachment_names(folder: str, attachments=None) -> list:
    """
    Which files in a tender folder are real attachments.

    Scrapers that report what they saved are believed. For the older ones
    that don't, fall back to "everything that isn't a .txt" -- the folder
    also holds extracted text written next to each original, and the two
    are otherwise indistinguishable. The fallback misses genuine .txt
    attachments, which is why the manifest is the preferred path.
    """
    if attachments is not None:
        return [a["file_name"] if isinstance(a, dict) else a for a in attachments]

    return sorted(
        name
        for name in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, name))
        and not name.lower().endswith(".txt")
    )


def upload_tender_attachments(folder, source_id, tender_ref, attachments=None, bucket=None):
    """
    Copy every attachment in one tender folder to the bucket.

    Call this after text extraction and before the temp directory is torn
    down. Each local file is deleted immediately after it is stored, so
    only one attachment is ever held at a time -- a tender pack with a
    video in it would otherwise be enough to exhaust the job's memory.
    """
    records = []
    lookup = {
        a["file_name"]: a
        for a in (attachments or [])
        if isinstance(a, dict) and "file_name" in a
    }

    for file_name in attachment_names(folder, attachments):
        local_path = os.path.join(folder, file_name)
        if not os.path.isfile(local_path):
            logger.warning(f"{file_name} was reported but is not on disk")
            continue

        record = upload_attachment(
            local_path,
            source_id=source_id,
            tender_ref=tender_ref,
            bucket=bucket,
            content_type=lookup.get(file_name, {}).get("content_type"),
        )
        if not record:
            # Leave the file alone. It goes when the temp directory does, but
            # deleting it here would destroy the only copy of something we
            # failed to store and have no record of.
            continue

        records.append(record)

        if not KEEP_LOCAL_COPIES:
            try:
                os.remove(local_path)
            except OSError as e:
                logger.warning(f"Could not free {file_name}: {e}")

    logger.info(f"{tender_ref}: stored {len(records)} attachment(s)")
    return records
