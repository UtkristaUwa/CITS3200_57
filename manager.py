import os
import sys
import tempfile
import logging
import smtplib
import json
import gc
import shutil
import signal
import time
from concurrent.futures import ThreadPoolExecutor

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
from error_scrapers import common, reporting
from processing.runtime_config import prepare_runtime_config
from email.message import EmailMessage
from datetime import datetime, timezone
from google.cloud import storage

#MIGHT NOT NEED THIS ONE, BUT SOMETHING BROKE WHEN I REMOVED IT SO ITS HERE
FAILURE_CODES = {
    common.SITE_TOTAL_FAILURE,
    common.SITE_LOGIN_FAILED,
    common.SITE_BOT_BLOCKED,
    common.SITE_STRUCTURE_CHANGE,
    common.SITE_RATE_LIMITED,
}
# Map status codes to human-readable explanations
ERROR_DESCRIPTIONS = {
    common.SITE_SUCCESS: "Scraping was a total success.",
    common.SITE_TOTAL_FAILURE: "The URL provided could not be reached.",
    common.SITE_LOGIN_FAILED: "The site login / portal authentication failed.",
    common.SITE_BOT_BLOCKED: (
        "Anti-bot detections (Cloudflare/reCAPTCHA) have blocked access."
    ),
    common.SITE_STRUCTURE_CHANGE: (
        "The HTML structure of the website changed; scraper selectors failed."
    ),
    common.SITE_RATE_LIMITED: "Site rate limited or temporarily blocked access.",
    common.TENDER_PARTIAL: (
        "Partial tender information gathered; requires manual verification."
    ),
}
# Map status codes to Frontend Table attributes (Status chip text and MUI color)
STATUS_DISPLAY = {
    common.SITE_SUCCESS: ("Success", "success"),
    common.TENDER_PARTIAL: ("Failed to download", "warning"),
    common.SITE_TOTAL_FAILURE: ("Error", "error"),
    common.SITE_LOGIN_FAILED: ("Error", "error"),
    common.SITE_BOT_BLOCKED: ("Error", "error"),
    common.SITE_STRUCTURE_CHANGE: ("Error", "error"),
    common.SITE_RATE_LIMITED: ("Error", "error"),
}
# Target portal URLs for the table link
PORTAL_URL_MAP = {
    "grantconnect": "https://www.grants.gov.au",
    "buynsw": "https://buy.nsw.gov.au",
    "tenders_act": "https://www.tenders.act.gov.au",
    "austender": "https://www.tenders.gov.au",
    "nt_qtol": "https://tendersonline.nt.gov.au",
    "qld_qtenders": "https://qtenders.hpw.qld.gov.au",
    "vic_buyingfor": "https://www.tenders.vic.gov.au",
}
def explain_code(code: int | None) -> tuple[str, str, str]:
  """Translates an error status code into:

  (text_explanation, frontend_label, chip_color).
  """
  if code is None:
    return ("No status code reported by scraper.", "Unknown", "default")

  desc = ERROR_DESCRIPTIONS.get(code, f"Unrecognized status code: {code}")
  label, chip_color = STATUS_DISPLAY.get(code, ("Unknown", "default"))
  return desc, label, chip_color

def publish_health_status_to_gcs(
    health_records: list[dict], bucket_name: str = "tenderai-dev-documents"
):
  """Uploads the scraper health summary JSON directly into your Google Cloud Storage bucket.

  The React frontend fetches this JSON directly to render the System Health
  table.
  """
  try:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob("scraper_health.json")

    # Upload formatted JSON
    blob.upload_from_string(
        data=json.dumps(health_records, indent=2),
        content_type="application/json",
    )

    # Allow React frontend to read the file over standard HTTPS
    try:
      blob.make_public()
    except Exception as perm_err:
      # If uniform bucket-level access is on, public access is managed at bucket level
      logger.debug(f"make_public skipped: {perm_err}")

    logger.info(
        f"✅ Published scraper health status ({len(health_records)} records) to"
        f" gs://{bucket_name}/scraper_health.json"
    )
  except Exception as e:
    logger.error(f"❌ Failed to publish health status JSON to Cloud Storage: {e}")

# Copies each tender's original attachments into Cloud Storage before the
# temporary directory (and everything in it) is deleted.
import attachment_store

# Import the BigQuery upload function
from ingestion.bigquery_client import get_client, upsert_tender, TENDERS_TABLE
#for ved embedding in tables
from google import genai
from google.cloud import bigquery

# Initialize BigQuery client
bq_client = get_client()

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("Manager")

# httpx logs every request at INFO, which buries the [SITE] lines and prints
logging.getLogger("httpx").setLevel(logging.WARNING)

# 0 = no cap, which is what the daily run wants. Override it per execution for
# a smoke test, without touching the job definition or this file:
#   gcloud run jobs execute tender-batch-job --region australia-southeast1 \
#     --update-env-vars SCRAPE_LIMIT_CLOUD=1 --wait
SCRAPE_LIMIT_CLOUD = int(os.environ.get("SCRAPE_LIMIT_CLOUD", "0"))
SCRAPE_LIMIT_LOCAL = 2
LOCAL_OUTPUT_DIR = "tenders_data"

# Every scraper the daily run should execute, paired with the source_id that
# identifies its portal in BigQuery and in the storage bucket's paths.
SCRAPERS = [
    ("grantconnect", run_grantconnect),
    ("buynsw", run_buynsw),
    ("tenders_act", run_act),
    ("austender", run_austender),
    ("nt_qtol", run_nt_qtol),
    ("qld_qtenders", run_qld_qtenders),
    ("vic_buyingfor", run_vic_buyingfor),
]

# Used for any tender folder no scraper claimed -- shouldn't happen, but a
# stray folder should not end up filed under the wrong portal.
# Sources that can't be read in one run (buy.nsw's WAF stops us after a few requests).
# Their scraper gets the URLs already in BigQuery and resumes from a saved listing page.
# Not applied to --local runs.
CRAWL_RESUME_SOURCES = {"buynsw"}

UNKNOWN_SOURCE_ID = "unknown"

# How many tenders stage 3 works on at once. Nearly all of its time is spent
# waiting on Gemini and BigQuery, so threads are enough. Set PIPELINE_WORKERS=1
# to process one tender at a time, as before.
DEFAULT_PIPELINE_WORKERS = 4


def _worker_count():
    try:
        return max(1, int(os.environ.get("PIPELINE_WORKERS", DEFAULT_PIPELINE_WORKERS)))
    except ValueError:
        return DEFAULT_PIPELINE_WORKERS

def _stage(number, total, title):
    """Divider between pipeline stages so the Cloud Run log is easy to scan."""
    logger.info(reporting.RULE)
    logger.info(f"STAGE {number}/{total}: {title}")
    logger.info(reporting.RULE)


def _known_source_urls(source_id):
    """source_urls already stored for this source. Empty set if the lookup fails."""
    try:
        job = bq_client.query(
            f"SELECT DISTINCT source_url FROM `{TENDERS_TABLE}` "
            "WHERE source_id = @sid AND source_url IS NOT NULL",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("sid", "STRING", source_id)]),
        )
        urls = {row.source_url for row in job.result()}
        logger.info(f"{source_id}: {len(urls)} tender URL(s) already in the DB")
        return urls
    except Exception as e:
        logger.warning(f"{source_id}: could not load known URLs ({e}); treating none as stored")
        return set()

def _load_process_tender(runtime_directory):
    """Prepare the startup CFG before importing the module that consumes it."""
    runtime = prepare_runtime_config(runtime_directory)
    if runtime.active:
        logger.info(f"Runtime tender processor configuration active: {runtime.path}")
    else:
        logger.warning(
            "Using repository tender processor configuration fallback: "
            f"{runtime.reason}"
        )

    # tender_processor calls load_config() during import. This import must stay
    # after prepare_runtime_config so the selected local file is loaded first.
    # GCS is checked once per job; changes made mid-run apply to the next run.
    from processing.tender_processor import process_tender

    return process_tender


def _load_determine_relevance():
    """Import relevance processing after the runtime CFG has been prepared."""
    from processing.relevance_determination import determine_relevance

    return determine_relevance


def _site_code(result):
    """Status code from a scraper's common.ScrapeResult, or None if it
    returned anything else."""
    if isinstance(result, common.ScrapeResult):
        return result.code
    return None


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
            model="text-embedding-004",  # todo change to be in config file
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
        # The scrapers save the full extraction as <file name>.txt (e.g.
        # Form.docx.txt: headers, footers, tables, spreadsheets). The older
        # document_scraper stage saves <base>.txt for PDF/DOCX only and reads
        # body paragraphs alone. Prefer the fuller one, fall back to the other.
        extracted_text = extracted_text_by_txt_name.get(f"{record['file_name']}.txt")
        if not extracted_text:
            extracted_text = extracted_text_by_txt_name.get(f"{base}.txt")
        doc_entry = {
            "file_name": record.get("file_name"),
            "file_type": record.get("file_type"),
            "storage_uri": record.get("storage_uri"),
            "extracted_text": extracted_text,
        }
        merged.append(doc_entry)
    return merged


def _folders_in(directory):
    return {
        name for name in os.listdir(directory)
        if os.path.isdir(os.path.join(directory, name))
    }


# Names of the browser processes the scrapers start. Chrome and its driver can
# outlive the scraper that launched them and keep gigabytes of RAM for the rest
# of the run.
def _is_browser_process(name):
    return name == "uc_driver" or name.startswith("chrome")


def _kill_stray_browsers(proc_root="/proc", kill=os.kill):
    """
    Kill any Chrome / chromedriver a scraper left running. Called between
    portals so one portal's leaked browser can't hold its memory for the rest
    of the run.

    Only acts inside the container (RUNNING_IN_CONTAINER): on a developer's
    machine this would close their own Chrome. It reads /proc directly because
    the slim image has no ps or pkill. Returns how many processes it killed."""
    if os.environ.get("RUNNING_IN_CONTAINER", "").lower() not in ("1", "true", "yes"):
        return 0
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return 0

    killed = 0
    for entry in entries:
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(os.path.join(proc_root, entry, "comm"), encoding="utf-8") as f:
                name = f.read().strip()
        except OSError:
            continue
        if _is_browser_process(name):
            try:
                kill(int(entry), signal.SIGKILL)
                killed += 1
            except OSError:
                pass
    if killed:
        logger.info(f"Cleaned up {killed} leftover browser process(es)")
    return killed


def _scrape_one(source_id, scrape, temp_dir, limit, publish, now_iso):
    """
    Run one scraper into temp_dir.

    Scrapers that report what they saved are used directly. For the older
    ones that return nothing, we note which folders appeared while they
    were running and attribute those to them -- otherwise their tenders
    end up filed in the bucket under "unknown", with no way to tell later
    which portal they came from.

    Returns (manifest, failure, health_record, summary_row). manifest is
    {folder_name: (source_id, attachments_or_None, source_url_or_None)};
    failure is a (source_id, reason) tuple or None; health_record is the row
    for scraper_health.json; summary_row is (site, code, tenders scraped,
    seconds) for the end-of-run table."""
    logger.info(f"Executing scraper: {source_id}")
    before = _folders_in(temp_dir)
    started = time.monotonic()

    try:
        extra = {}
        if publish and source_id in CRAWL_RESUME_SOURCES:
            extra = {"known_urls": _known_source_urls(source_id), "resume": True}
        result = scrape(limit=limit, output_dir=temp_dir, **extra)
    except Exception as e:
        logger.error(f"Scraper '{source_id}' failed: {e}")
        health_record = {
            "website": source_id.upper(),
            "url": PORTAL_URL_MAP.get(source_id, "N/A"),
            "last_run": now_iso,
            "status": "Error",
            "status_color": "error",
            "message": f"Unhandled exception: {e}",
        }
        summary_row = (source_id.upper(), None, 0, time.monotonic() - started)
        return {}, (source_id, f"exception: {e}"), health_record, summary_row

    code = _site_code(result)
    scraped_count = result.count if code is not None else 0
    site_name = result.site if code is not None else source_id
    summary_row = (site_name.upper(), code, scraped_count, time.monotonic() - started)

    # 1. human-readable message, 2. table status text, 3. MUI chip color
    explanation, label, chip_color = explain_code(code)

    failure = None
    if code in FAILURE_CODES:
        logger.error(f"Scraper '{source_id}' reported failure: {explanation}")
        failure = (source_id, explanation)
    elif code == common.TENDER_PARTIAL:
        logger.warning(f"Scraper '{source_id}' returned partial data (code {code})")

    health_record = {
        "website": source_id.upper(),
        "url": PORTAL_URL_MAP.get(source_id, "N/A"),
        "last_run": now_iso,
        "status": label,
        "status_color": chip_color,
        "message": explanation,
    }

    manifest = {
        folder_name: (source_id, None, None)
        for folder_name in _folders_in(temp_dir) - before
    }
    return manifest, failure, health_record, summary_row


def run_scrapers(temp_dir, limit, publish=True):
    """
    Run every configured scraper into temp_dir, one after another.

    This is the scrape-only path (--local). The cloud pipeline in main() runs
    the same scrapers one portal at a time, processing and clearing each
    portal's tenders before starting the next.

    Returns (manifest, failures). manifest is
    {folder_name: (source_id, attachments_or_None, source_url_or_None)};
    failures is a list of (source_id, reason)."""
    manifest = {}
    failures = []
    health_records = []
    summary_rows = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for source_id, scrape in SCRAPERS:
        part, failure, health_record, summary_row = _scrape_one(
            source_id, scrape, temp_dir, limit, publish, now_iso
        )
        manifest.update(part)
        if failure:
            failures.append(failure)
        health_records.append(health_record)
        summary_rows.append(summary_row)

    # Upload the health_records list to Cloud Storage. This creates/overwrites
    # gs://tenderai-dev-documents/scraper_health.json
    if publish:
        publish_health_status_to_gcs(
            health_records, bucket_name="tenderai-dev-documents"
        )

    reporting.log_run_summary(summary_rows, logger)
    return manifest, failures


def _process_one_tender(position, total, tender_folder_name, temp_dir, scraped,
                        process_tender, determine_relevance):
    """Store one tender's attachments, AI-process it and upsert it to BigQuery."""
    tender_path = os.path.join(temp_dir, tender_folder_name)
    source_id, attachments, source_url = scraped.get(
        tender_folder_name, (UNKNOWN_SOURCE_ID, None, None)
    )
    logger.info(
        f"---- tender {position}/{total}: "
        f"{tender_folder_name}  [{source_id.upper()}] ----"
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
    logger.info(f"⚡ Processing tender: {tender_folder_name}...")
    current_tender = None
    try:
        current_tender = process_tender(tender_path)
        if current_tender is not None:
            current_tender["source_id"] = source_id
            source_url = common.read_source_url(tender_path) or source_url
            if source_url:
                current_tender["source_url"] = source_url
            # The scraper's own reference (the folder name), never Gemini's guess:
            # the model's value changed between runs and produced duplicate rows.
            current_tender["source_reference_id"] = tender_folder_name
            current_tender["documents"] = _merge_document_records(
                documents, current_tender.get("documents") or []
            )
    except Exception as e:
        logger.error(f"Tender processing failed for {tender_folder_name}: {e}")
        return

    if current_tender is None:
        logger.warning(f"Tender processing returned None for {tender_folder_name}, skipping.")
        return

    # 4c. Score the tender against the focus area / work type taxonomies.
    # The enriched record (processed fields + focus_areas/work_types/fit/
    # fit_reason) is what gets upserted below. Unlike a processing
    # failure, a scoring failure doesn't drop the tender: it still goes
    # to BigQuery, just with the relevance fields left null.
    logger.info(f"🎯 Determining relevance for {tender_folder_name}...")
    try:
        current_tender = determine_relevance(current_tender)
        logger.info(
            f"Fit score for {tender_folder_name}: {current_tender.get('fit')} "
            f"(focus_areas={current_tender.get('focus_areas')}, "
            f"work_types={current_tender.get('work_types')})"
        )
    except Exception as e:
        logger.error(f"Relevance determination failed for {tender_folder_name}: {e}")

    # todo generate embeddings
    logger.info(f"Generating Gemini Embedding 🔍 for {tender_folder_name}...")

    # combine fields that we vectorise
    title = current_tender.get("title") or ""
    summary = current_tender.get("description") or ""

    embed = f"Title: {title}. Summary: {summary}".strip()

    # assign embedded information to current tender
    current_tender["embedding"] = generate_embedding(embed)

    # Try to upload to BigQuery
    logger.info(f"Uploading processed tender {tender_folder_name} to BigQuery...")
    try:
        result = upsert_tender(bq_client, current_tender)
        logger.info(
            f"BigQuery upsert result for {tender_folder_name}: "
            f"{result['action']} (id={result['tender_id']})"
        )
    except Exception as e:
        logger.error(f"Failed to upsert to BigQuery: {e}")
        return

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



def _process_tenders(temp_dir, tender_folders, scraped, process_tender, determine_relevance):
    """
    Store each tender's attachments, AI-process it and upsert it to BigQuery,
    PIPELINE_WORKERS tenders at a time. Each tender has its own folder and the
    Gemini / BigQuery / Storage clients are safe to share between threads.

    Returns how many tenders raised an error nothing else handles. One bad
    tender is logged and the rest carry on; the caller reports the count."""
    total = len(tender_folders)
    workers = min(_worker_count(), total)

    def run_one(position, name):
        try:
            _process_one_tender(position, total, name, temp_dir, scraped,
                                process_tender, determine_relevance)
            return 0
        except Exception:
            logger.exception(f"Unexpected error while processing {name}; carrying on with the rest")
            return 1

    if workers <= 1:
        return sum(run_one(position, name)
                   for position, name in enumerate(tender_folders, start=1))

    logger.info(f"Processing {total} tender(s), {workers} at a time")
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tender") as pool:
        results = pool.map(run_one, range(1, total + 1), tender_folders)
        return sum(results)


def _free_memory():
    """
    Give back what the portal just finished with: collect garbage, then ask
    glibc to return freed memory to the OS (Python alone often doesn't).
    """
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass  # not glibc (macOS, musl): nothing more to do

def main():
    if "--local" in sys.argv[1:]:
        os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
        _stage(1, 1, f"LOCAL RUN: scrape only, {SCRAPE_LIMIT_LOCAL} tender(s) per site "
                     f"-> {LOCAL_OUTPUT_DIR}/  (no processing, no BigQuery, no GCS)")
        run_scrapers(LOCAL_OUTPUT_DIR, SCRAPE_LIMIT_LOCAL, publish=False)
        return

    logger.info("Starting Daily Tender Pipeline...")

    failures = []
    health_records = []
    summary_rows = []
    total_tenders = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    # Spin up a temporary ephemeral directory in container memory
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"Created temporary working directory: {temp_dir}")

        # Keep the downloaded runtime CFG inside this job-wide directory. It
        # remains available for tender_processor's local mtime checks until all
        # tender processing has finished.
        process_tender = _load_process_tender(temp_dir)
        determine_relevance = _load_determine_relevance()

        # One portal at a time: scrape it, extract text, store + AI-process +
        # upsert its tenders, then clear its folders before the next portal.
        # Everything lives in RAM, so this keeps the peak at the largest single
        # portal instead of every portal at once -- and the portals already
        # finished are in BigQuery if a later one dies.
        for source_id, scrape in SCRAPERS:
            # 1. Run the web scraper. It downloads the tender page text and
            # attachments into temp_dir.
            _stage(1, 4, f"SCRAPE {source_id}")
            scraped, failure, health_record, summary_row = _scrape_one(
                source_id, scrape, temp_dir, SCRAPE_LIMIT_CLOUD, True, now_iso
            )
            _kill_stray_browsers()
            if failure:
                failures.append(failure)
            health_records.append(health_record)
            summary_rows.append(summary_row)
            # Published after every portal, not once at the end, so the health
            # table is current while the long processing stages run.
            publish_health_status_to_gcs(
                health_records, bucket_name="tenderai-dev-documents"
            )

            tender_folders = sorted(_folders_in(temp_dir))
            if not tender_folders:
                logger.warning(f"{source_id}: no tenders were scraped. Nothing to process.")
                continue

            try:
                # 2. Run the Document Scraper
                # It scans temp_dir, parses PDFs/DOCXs, and creates individual .txt files
                _stage(2, 4, f"EXTRACT text from attachments ({source_id})")
                logger.info("📄 Executing Document Scraper...")
                try:
                    run_doc_scraper(temp_dir)
                except Exception as e:
                    # Not fatal: tenders still have their page text, so the AI stage can
                    # work from that alone, and the attachments are still worth storing.
                    logger.error(f"Document scraper failed: {e}")

                # 3. Store attachments, then hand each tender to AI processing
                _stage(3, 4, f"STORE attachments, AI-process, upsert "
                             f"{source_id} ({len(tender_folders)} tender(s))")
                logger.info("🤖 Preparing data for AI Processing...")
                errors = _process_tenders(
                    temp_dir, tender_folders, scraped, process_tender, determine_relevance
                )
                if errors:
                    failures.append(
                        (source_id, f"{errors} tender(s) hit an unexpected error while processing")
                    )
            finally:
                for folder_name in tender_folders:
                    shutil.rmtree(os.path.join(temp_dir, folder_name), ignore_errors=True)
                _free_memory()
                logger.info(f"{source_id}: cleared {len(tender_folders)} tender folder(s) "
                            "from the working directory")

            total_tenders += len(tender_folders)

        reporting.log_run_summary(summary_rows, logger)
        if not total_tenders:
            logger.error("No tenders were scraped. Nothing was processed.")
            sys.exit(1)

    # Once the 'with' block ends, Python permanently deletes the temp_dir and all files inside it.
    _stage(4, 4, "FINISH")
    logger.info("Pipeline finished. Temporary files wiped from memory.")
    if failures:
        summary = ", ".join(f"{s} ({why})" for s, why in failures)
        logger.error(f"Pipeline finished with scraper failures: {summary}")

        #send alert email with gcs alerts that detect the previous error and email admins


        os._exit(1)
    os._exit(0)


if __name__ == "__main__":
    main()
