import os
import sys
import tempfile
import logging

# Import your web scraper and document scraper functions
# (Adjust the import names to match your actual python files)
from web_scrapers.webscraperinit import run_scraper as run_austender
from document_scraper.main import process_tenders as run_doc_scraper

# Improt tender processing code
from data_ingestion.tender_processor import process_tender

# Copies each tender's original attachments into Cloud Storage before the
# temporary directory (and everything in it) is deleted.
import attachment_store

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("Manager")

SCRAPE_LIMIT = int(os.environ.get("SCRAPE_LIMIT", "10"))

# Every scraper the daily run should execute, paired with the source_id that
# identifies its portal in BigQuery and in the storage bucket's paths.
SCRAPERS = [
    ("austender", run_austender),
]

# Used for any tender folder no scraper claimed -- shouldn't happen, but a
# stray folder should not end up filed under the wrong portal.
UNKNOWN_SOURCE_ID = "unknown"


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
         tender.get("attachments"))
        for tender in (tenders or [])
        if tender.get("folder")
    ]


def run_scrapers(temp_dir):
    """
    Run every configured scraper into temp_dir.

    Returns {folder_name: (source_id, attachments_or_None)}. One scraper
    failing no longer stops the run -- with several portals configured,
    losing all of them because one site changed its markup is worse than
    an incomplete day.
    """
    manifest = {}

    for source_id, scrape in SCRAPERS:
        logger.info(f"Executing scraper: {source_id}")
        try:
            result = scrape(limit=SCRAPE_LIMIT, output_dir=temp_dir)
        except Exception as e:
            logger.error(f"Scraper '{source_id}' failed: {e}")
            continue

        for folder_name, attachments in _folders_and_attachments(result):
            manifest[folder_name] = (source_id, attachments)

    return manifest


def main():
    logger.info("Starting Daily Tender Pipeline...")

    # 1. Spin up a temporary ephemeral directory in container memory
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"Created temporary working directory: {temp_dir}")

        # 2. Run the Web Scrapers
        # We pass the temp_dir so they download HTML metadata and PDFs directly
        # into RAM
        scraped = run_scrapers(temp_dir)

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
            source_id, attachments = scraped.get(
                tender_folder_name, (UNKNOWN_SOURCE_ID, None)
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
            try:
                tender = process_tender(tender_path)
            except Exception as e:
                logger.error(f"Tender processing failed for {tender_folder_name}: {e}")
                continue

            # ==================================================================
            # TODO: write `tender` and `documents` to BigQuery.
            #
            # `documents` is already shaped for the `documents` column: one
            # entry per attachment with file_name, file_type, content_type,
            # size_bytes, checksum_sha256, storage_uri and uploaded_at. The
            # last five need adding to the table first -- see migration 002
            # for the pattern.
            # ==================================================================
            print(tender)
            logger.info(
                f"{tender_folder_name}: {len(documents)} document(s) stored, "
                f"awaiting database write"
            )

    # Once the 'with' block ends, Python permanently deletes the temp_dir and all files inside it.
    logger.info("Pipeline finished. Temporary files wiped from memory.")


if __name__ == "__main__":
    main()
