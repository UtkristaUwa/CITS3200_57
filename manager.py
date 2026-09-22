import os
import sys
import tempfile
import logging

# Import your web scraper and document scraper functions
# (Adjust the import names to match your actual python files)
from web_scrapers.webscraperinit import run_scraper as run_austender
from error_scrapers.grant_connect.scraper import run_scraper as run_grantconnect
from error_scrapers.buy_nsw.scraper import run_scraper as run_buynsw
from error_scrapers.tenders_act.scraper import run_scraper_via_browser as run_act
from document_scraper.main import process_tenders as run_doc_scraper

# Improt tender processing code
from processing.tender_processor import process_tender

# Copies each tender's original attachments into Cloud Storage before the
# temporary directory (and everything in it) is deleted.
import attachment_store

# Import the BigQuery upload function
from ingestion.bigquery_client import get_client, upsert_tender

#for ved embedding in tables
from google import genai
from google.cloud import bigquery

# Initialize BigQuery client
bq_client = get_client()

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("Manager")

SCRAPE_LIMIT = int(os.environ.get("SCRAPE_LIMIT", "30"))

# Every scraper the daily run should execute, paired with the source_id that
# identifies its portal in BigQuery and in the storage bucket's paths.
SCRAPERS = [
    #("austender", run_austender),
    ("grantconnect", run_grantconnect),
    ("buynsw", run_buynsw),
    ("tenders_act", run_act),
]

# Used for any tender folder no scraper claimed -- shouldn't happen, but a
# stray folder should not end up filed under the wrong portal.
UNKNOWN_SOURCE_ID = "unknown"

# Initialize the Vertex AI Gemini client using existing ADC credentials
ai_client = genai.Client(
    vertexai=True,
    project="tenderai-dev",
    location="australia-southeast1",
)
def generate_embedding(text: str) -> list[float]:
    """
    Converts the ai summary into a 768-dimensional float vector
    Safely truncated to 2000 characters to respect token limits.
    """
    if not text or not text.strip():
        logger.warning("Empty text passed to generate_embedding; returning empty vector.")
        return []
    try:
        response = ai_client.models.embed_content(
            model="text-embedding-004",#todo change to be in config file
            contents=text[:2000]
        )
        # Log vector diagnostics
        values = response.embeddings[0].values
        logger.info(
            f"Generated embedding: {len(values)} dimensions. "
            f"Preview (first 5): {[round(x, 4) for x in values[:5]]} | "
            f"Range: [{round(min(values), 4)}, {round(max(values), 4)}]"
        )
        return response.embeddings[0].values
    except Exception as err:
        logger.error(f"Failed to generate embedding: {err}")
        return []

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


def _folders_in(directory):
    return {
        name for name in os.listdir(directory)
        if os.path.isdir(os.path.join(directory, name))
    }


def run_scrapers(temp_dir):
    """
    Run every configured scraper into temp_dir.

    Returns {folder_name: (source_id, attachments_or_None)}. One scraper
    failing no longer stops the run -- with several portals configured,
    losing all of them because one site changed its markup is worse than
    an incomplete day.

    Scrapers that report what they saved are used directly. For the older
    ones that return nothing, we note which folders appeared while they
    were running and attribute those to them -- otherwise their tenders
    end up filed in the bucket under "unknown", with no way to tell later
    which portal they came from.
    """
    manifest = {}

    for source_id, scrape in SCRAPERS:
        logger.info(f"Executing scraper: {source_id}")
        before = _folders_in(temp_dir)

        try:
            result = scrape(limit=SCRAPE_LIMIT, output_dir=temp_dir)
        except Exception as e:
            logger.error(f"Scraper '{source_id}' failed: {e}")
            continue

        for folder_name in _folders_in(temp_dir) - before:
            manifest[folder_name] = (source_id, None)

        # A reported manifest is better than the guess above, so it wins.
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
            logger.info("Processing tender...")
            current_tender = None
            try:
                current_tender = process_tender(tender_path)
                if current_tender is not None:
                    current_tender["source_id"] = source_id
            except Exception as e:
                logger.error(f"Tender processing failed for {tender_folder_name}: {e}")
                continue
            # todo generate embeddings
            logger.info(f"Generating Gemini Embedding 🔍 for {tender_folder_name}...")

            # combine fields that we vectorise
            title = current_tender.get("title") or ""
            summary = current_tender.get("description") or ""

            embed = f"Title: {title}. Summary: {summary}".strip()

            # assign embedded information to current tender
            current_tender["embedding"] = generate_embedding(embed)

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


if __name__ == "__main__":
    main()