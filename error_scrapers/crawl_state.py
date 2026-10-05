"""Tiny persisted state for crawlers that cannot finish a whole site in one run."""

import json
import logging

BUCKET = "tenderai-dev-documents"
logger = logging.getLogger("crawl_state")


def _blob(source_id):
    from google.cloud import storage
    return storage.Client().bucket(BUCKET).blob(f"scraper_state/{source_id}.json")


def load_page(source_id: str) -> int:
    """Listing page to start from (1 if nothing saved or GCS can't be read)."""
    try:
        blob = _blob(source_id)
        if blob.exists():
            return max(1, int(json.loads(blob.download_as_text()).get("next_page", 1)))
    except Exception as exc:
        logger.warning("could not load crawl cursor for %s (%s); starting at page 1", source_id, exc)
    return 1


def save_page(source_id: str, page: int) -> None:
    try:
        _blob(source_id).upload_from_string(
            json.dumps({"next_page": page}), content_type="application/json")
    except Exception as exc:
        logger.warning("could not save crawl cursor for %s (%s)", source_id, exc)