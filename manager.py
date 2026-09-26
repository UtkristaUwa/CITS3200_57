import os
import sys
import tempfile
import logging

# Import your web scraper and document scraper functions
# (Adjust the import names to match your actual python files)
from error_scrapers.austender.scraper import run_scraper as run_austender
from error_scrapers.nt_qtol.scraper import run_scraper as run_nt_qtol
from error_scrapers.qld_qtenders.scraper import run_scraper as run_qld_qtenders
from error_scrapers.vic_buyingfor.scraper import run_scraper as run_vic_buyingfor
from error_scrapers.grant_connect.scraper import run_scraper as run_grantconnect
from error_scrapers.buy_nsw.scraper import run_scraper as run_buynsw
from error_scrapers.tenders_act.scraper import run_scraper_via_browser as run_act
from document_scraper.main import process_tenders as run_doc_scraper
from error_scrapers import common

FAILURE_CODES = {
    common.SITE_TOTAL_FAILURE,
    common.SITE_LOGIN_FAILED,
    common.SITE_BOT_BLOCKED,
    common.SITE_STRUCTURE_CHANGE,
    common.SITE_RATE_LIMITED,
}

# Improt tender processing code
from processing.tender_processor import process_tender

# Copies each tender's original attachments into Cloud Storage before the
# temporary directory (and everything in it) is deleted.
import attachment_store

# Import the BigQuery upload function
from ingestion.bigquery_client import get_client, upsert_tender

# Initialize BigQuery client
bq_client = get_client()

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("Manager")

SCRAPE_LIMIT = int(os.environ.get("SCRAPE_LIMIT", "10"))

# Every scraper the daily run should execute, paired with the source_id that
# identifies its portal in BigQuery and in the storage bucket's paths.
SCRAPERS = [
    ("austender", run_austender),
    ("grantconnect", run_grantconnect),
    ("buynsw", run_buynsw),
    ("tenders_act", run_act),
    ("nt-qtol", run_nt_qtol),
    ("qld-qtenders", run_qld_qtenders),
    ("vic-buyingfor", run_vic_buyingfor),
]

# Used for any tender folder no scraper claimed -- shouldn't happen, but a
# stray folder should not end up filed under the wrong portal.
UNKNOWN_SOURCE_ID = "unknown"

def _site_code(result):
    """Status code from a (code, tenders) result, or None for scrapers
    that return nothing."""
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], int):
        return result[0]
    return None


def _folders_and_attachments(result):
    """
    Read a scraper's return value.

    Newer scrapers return (status_code, tenders), where each tender lists
    the attachment files it actually saved. Older ones return None and are
    handled by falling back to a directory scan later on.
    """
    if not isinstance(result, tuple) or len(result) != 2:
        return []
    _, tenders = result
    return [
        (os.path.basename(str(tender.get("folder", "")).rstrip("/")),
         tender.get("attachments"),
         tender.get("source_url"))
        for tender in (tenders or [])
        if tender.get("folder")
    ]


def _merge_document_records(attachment_records, txt_documents):
    """
    Combine attachment_store's storage records (file_name, storage_uri,
    file_type, checksum, ...) with the extracted text tender_processor read
    from each attachment's sibling <base>.txt into one row per real
    attachment -- the shape the `documents` column needs to be both
    findable (storage_uri) and searchable (extracted_text).

    txt_documents entries with no matching attachment -- the tender's own
    <REF>.txt page text, or an extraction whose attachment failed to
    upload -- are dropped here rather than carried into `documents`:
    neither is a real, downloadable attachment.
    """
    extracted_text_by_txt_name = {
        doc["file_name"]: doc.get("extracted_text") for doc in txt_documents
    }

    merged = []
    for record in attachment_records:
        record = dict(record)
        base, _ext = os.path.splitext(record["file_name"])
        record["extracted_text"] = extracted_text_by_txt_name.get(f"{base}.txt")
        merged.append(record)
    return merged


def _folders_in(directory):
    return {
        name for name in os.listdir(directory)
        if os.path.isdir(os.path.join(directory, name))
    }


def run_scrapers(temp_dir):
    """
    Run every configured scraper into temp_dir.

    Scrapers that report what they saved are used directly. For the older
    ones that return nothing, we note which folders appeared while they
    were running and attribute those to them -- otherwise their tenders
    end up filed in the bucket under "unknown", with no way to tell later
    which portal they came from.

    Returns (manifest, failures). manifest is
    {folder_name: (source_id, attachments_or_None, source_url_or_None)};
    failures is a list of (source_id, reason)."""
    manifest = {}
    failures = []

    for source_id, scrape in SCRAPERS:
        logger.info(f"Executing scraper: {source_id}")
        before = _folders_in(temp_dir)

        try:
            result = scrape(limit=SCRAPE_LIMIT, output_dir=temp_dir)
        except Exception as e:
            logger.error(f"Scraper '{source_id}' failed: {e}")
            failures.append((source_id, f"exception: {e}"))
            continue

        code = _site_code(result)
        if code in FAILURE_CODES:
            logger.error(f"Scraper '{source_id}' reported failure code {code}")
            failures.append((source_id, f"code {code}"))
        elif code == common.TENDER_PARTIAL:
            logger.warning(f"Scraper '{source_id}' returned partial data (code {code})")

        for folder_name in _folders_in(temp_dir) - before:
            manifest[folder_name] = (source_id, None, None)

        for folder_name, attachments, source_url in _folders_and_attachments(result):
            manifest[folder_name] = (source_id, attachments, source_url)

    return manifest, failures

def main():
    logger.info("Starting Daily Tender Pipeline...")

    # 1. Spin up a temporary ephemeral directory in container memory
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"Created temporary working directory: {temp_dir}")

        # 2. Run the Web Scrapers
        # We pass the temp_dir so they download HTML metadata and PDFs directly
        # into RAM
        scraped, failures = run_scrapers(temp_dir)

        tender_folders = sorted(
            name for name in os.listdir(temp_dir)
            if os.path.isdir(os.path.join(temp_dir, name))
        )
        if not tender_folders:
            logger.error("No tenders were scraped. Nothing to process.")
            sys.exit(1)

        # 3. Run the Document Scraper
        # It scans temp_dir, parses PDFs/DOCXs, and creates individual .txt files
        logger.info("Executing Document Scraper...")
        try:
            run_doc_scraper(temp_dir)
        except Exception as e:
            # Not fatal: tenders still have their page text, so the AI stage can
            # work from that alone, and the attachments are still worth storing.
            logger.error(f"Document scraper failed: {e}")

        # 4. Store attachments, then hand each tender to AI processing
        logger.info("Preparing data for AI Processing...")

        for tender_folder_name in tender_folders:
            tender_path = os.path.join(temp_dir, tender_folder_name)
            source_id, attachments, source_url = scraped.get(
                tender_folder_name, (UNKNOWN_SOURCE_ID, None, None)
            )

            # 4a. Copy the originals into Cloud Storage and drop the local
            # copies. This runs before AI processing for two reasons: the
            # files are gone the moment this block exits, and freeing each
            # one as we go keeps a large attachment from exhausting the
            # job's memory. process_tender only reads the .txt files, so
            # removing the originals costs it nothing.
            documents = attachment_store.upload_tender_attachments(
                tender_path,
                source_id=source_id,
                tender_ref=tender_folder_name,
                attachments=attachments,
            )

            # 4b. Run tender processing on current tender
            logger.info("Processing tender...")
            current_tender = None
            try:
                current_tender = process_tender(tender_path)
                if current_tender is not None:
                    current_tender["source_id"] = source_id
                    if source_url:
                        current_tender["source_url"] = source_url
                    current_tender["documents"] = _merge_document_records(
                        documents, current_tender.get("documents") or []
                    )
            except Exception as e:
                logger.error(f"Tender processing failed for {tender_folder_name}: {e}")
                continue

            # Try to upload to BigQuery
            logger.info("Uploading processed tender to BigQuery...")
            try:
                result = upsert_tender(bq_client, current_tender)
                logger.info(
                    f"BigQuery upsert result for {tender_folder_name}: "
                    f"{result['action']} (id={result['tender_id']})"
                )
            except Exception as e:
                logger.error(f"Failed to upsert to BigQuery: {e}")
                continue

            # Deliberately not printing `current_tender` in full: its
            # documents carry the entire extracted text of every attachment,
            # which is megabytes of Cloud Logging per run. Set
            # PIPELINE_DEBUG=on when you actually need to eyeball it.
            if os.environ.get("PIPELINE_DEBUG", "off").lower() in {"on", "true", "1"}:
                print(current_tender)

            logger.info(
                f"{tender_folder_name} [{source_id}]: "
                f"{len(documents)} document(s) stored, "
                f"title={current_tender.get('title')!r}"
            )

    # Once the 'with' block ends, Python permanently deletes the temp_dir and all files inside it.
    logger.info("Pipeline finished. Temporary files wiped from memory.")
    if failures:
        summary = ", ".join(f"{s} ({why})" for s, why in failures)
        logger.error(f"Pipeline finished with scraper failures: {summary}")
        os._exit(1)
    os._exit(0)


if __name__ == "__main__":
    main()