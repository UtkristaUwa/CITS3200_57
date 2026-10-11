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
from error_scrapers.eTender.scraper import run_scraper as run_qbuild_etender
from error_scrapers.vic_buyingfor.scraper import run_scraper as run_vic_buyingfor
from error_scrapers.grant_connect.scraper import run_scraper as run_grantconnect
from error_scrapers.buy_nsw.scraper import run_scraper as run_buynsw
from error_scrapers.tenders_act.scraper import run_scraper_via_browser as run_act
from error_scrapers.tenders_wa.scraper import run_scraper as run_tenders_wa
from error_scrapers.sa_tenders.scraper import run_scraper as run_sa_tenders
from error_scrapers.tenders_tas.scraper import run_scraper as run_tenders_tas
from error_scrapers import common, reporting, run_notes
from processing.runtime_config import prepare_runtime_config
from email.message import EmailMessage
from datetime import date, datetime, timezone
from decimal import Decimal
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
        "Some tenders are incomplete; see the run logs for which."
    ),
}
# Map status codes to Frontend Table attributes (Status chip text and MUI color)
STATUS_DISPLAY = {
    common.SITE_SUCCESS: ("Success", "success"),
    common.TENDER_PARTIAL: ("Partial", "warning"),
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
    "qbuild_etender": "https://etender.hpw.qld.gov.au",
    "vic_buyingfor": "https://www.tenders.vic.gov.au",
    "tenders_wa": "https://www.tenders.wa.gov.au",
    "sa_tenders": "https://www.tenders.sa.gov.au",
    "tenders_tas": "https://www.tenders.tas.gov.au",
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

def _load_previous_health_records(
    bucket_name: str = "tenderai-dev-documents",
) -> dict[str, str | None]:
  """Loads existing scraper_health.json from Cloud Storage into a lookup map:

  {website_name_lowercase: last_successful_run_timestamp}.
  """
  try:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob("scraper_health.json")
    if not blob.exists():
      return {}
    content = blob.download_as_text()
    records = json.loads(content)
    if not isinstance(records, list):
      return {}
    result = {}
    for r in records:
      if isinstance(r, dict) and "website" in r:
        site = str(r["website"]).strip().lower()
        result[site] = r.get("last_successful_run")
    return result
  except Exception as e:
    logger.warning(f"Could not load previous health state from GCS: {e}")
    return {}

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
from ingestion.bigquery_client import ALL_COLUMNS, get_client, upsert_tender, TENDERS_TABLE
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
    ("qbuild_etender", run_qbuild_etender),
    ("vic_buyingfor", run_vic_buyingfor),
    ("tenders_wa", run_tenders_wa),
    ("sa_tenders", run_sa_tenders),
    ("tenders_tas", run_tenders_tas),

]

# Used for any tender folder no scraper claimed -- shouldn't happen, but a
# stray folder should not end up filed under the wrong portal.
# Sources that can't be read in one run (buy.nsw's WAF and VIC's Cloudflare stop us
# part-way). Their scraper gets the URLs already in BigQuery and carries on where the
# last run stopped: buy.nsw from a saved listing page, VIC by doing the tenders not
# stored yet first. Not applied to --local runs.
CRAWL_RESUME_SOURCES = {"buynsw", "vic_buyingfor", "sa_tenders"}

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


def _load_summariser():
    """Import the summarisation half of tender_processor, for reprocess runs.

    Same ordering rule as _load_process_tender: the runtime CFG must already be
    prepared, because tender_processor loads it during import.
    """
    from processing.tender_processor import (
        build_tender_context,
        gather_relevant_documents,
        summarise_tender,
    )

    return gather_relevant_documents, build_tender_context, summarise_tender


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
        # Form.docx.txt: headers, footers, tables, spreadsheets). <base>.txt is
        # only the attachment itself when it is a genuine .txt file.
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


def _scrape_one(source_id, scrape, temp_dir, limit, publish, now_iso, prev_success=None):
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
            "last_successful_run": prev_success,
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
    label, chip_color, explanation = run_notes.describe(
        code, scraped_count, run_notes.snapshot(), explanation, label, chip_color
    )

    failure = None
    if code in FAILURE_CODES:
        logger.error(f"Scraper '{source_id}' reported failure: {explanation}")
        failure = (source_id, explanation)
    elif code == common.TENDER_PARTIAL:
        logger.warning(f"Scraper '{source_id}' returned partial data (code {code})")

    last_success = (
        now_iso if (code == common.SITE_SUCCESS and label == "Success") else prev_success
    )

    health_record = {
        "website": source_id.upper(),
        "url": PORTAL_URL_MAP.get(source_id, "N/A"),
        "last_run": now_iso,
        "last_successful_run": last_success,
        "status": label,
        "status_color": chip_color,
        "message": explanation,
    }

    manifest = {
        folder_name: (source_id, None, None)
        for folder_name in _folders_in(temp_dir) - before
    }
    return manifest, failure, health_record, summary_row


def _refresh_health(health_record, summary_row, prev_success=None):
    """
    Re-word a scraper's health row once its tenders have been stored. The store
    stage can find problems the scrape could not see (a file over the size
    limit, a file that would not upload), and they count as incomplete tenders.
    """
    _site, code, count, _seconds = summary_row
    explanation, label, color = explain_code(code)
    label, color, message = run_notes.describe(
        code, count, run_notes.snapshot(), explanation, label, color
    )
    last_success = health_record.get("last_successful_run")
    if label != "Success" and prev_success is not None:
        last_success = prev_success
    return {
        **health_record,
        "status": label,
        "status_color": color,
        "message": message,
        "last_successful_run": last_success,
    }


def _refresh_summary_row(health_record, summary_row):
    """
    Keep the end-of-run table in step with the health row. A scraper that
    returned success is shown as partial once storing its tenders found an
    incomplete one; failure codes are left as they are.
    """
    site, code, count, seconds = summary_row
    if code == common.SITE_SUCCESS and health_record.get("status") == "Partial":
        return (site, common.TENDER_PARTIAL, count, seconds)
    return summary_row


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
    previous_health = _load_previous_health_records() if publish else {}

    for source_id, scrape in SCRAPERS:
        prev_success = previous_health.get(source_id.lower())
        part, failure, health_record, summary_row = _scrape_one(
            source_id, scrape, temp_dir, limit, publish, now_iso, prev_success=prev_success
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


# ==============================================================================
# Reprocess-only run
#
# Re-applies the AI layer to tenders already in BigQuery, without scraping
# anything. Triggered from the admin config page after a prompt change, via
# PIPELINE_MODE=reprocess on a job execution.
#
# It deliberately writes back only the AI-derived fields, plus location_states.
# The scraped landing page text is never persisted (attachment_store uploads
# attachments only, and _merge_document_records drops page text), so
# re-extracting title/dates/contacts here would read them out of attachments
# that mostly don't contain them and overwrite good values with nulls.
#
# location_states is the exception because it needs no model and no page: the
# stored `location` string names its states, so it can be re-derived offline
# and unioned into whatever is already there.
# ==============================================================================

REPROCESS_PROGRESS_OBJECT = os.environ.get(
    "REPROCESS_PROGRESS_OBJECT", "reprocess_progress.json"
)


def publish_reprocess_progress(payload: dict, bucket_name: str = "tenderai-dev-documents"):
    """Publish the reprocess run's progress for the admin page to poll.

    Same bucket and approach as publish_health_status_to_gcs: the API already
    reads small JSON blobs from here, so this needs no new storage or access.

    Never raises. A reprocess run that is working must not be abandoned because
    the progress file could not be written -- the admin page falls back to the
    live Cloud Run execution state, so the worst case is a run that shows as
    "in progress" with no counts.
    """
    try:
        blob = storage.Client().bucket(bucket_name).blob(REPROCESS_PROGRESS_OBJECT)
        blob.cache_control = "no-store"
        blob.upload_from_string(
            data=json.dumps({**payload, "updated_at": datetime.now(timezone.utc).isoformat()}),
            content_type="application/json",
        )
    except Exception as e:
        logger.warning(f"Could not publish reprocess progress: {e}")


def _json_safe_value(value):
    """BigQuery hands back DATE/TIMESTAMP/NUMERIC as date/datetime/Decimal.
    Convert them to what the scrape path would have produced, so the row can be
    re-uploaded and hashes the same way a freshly scraped one does."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_json_safe_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe_value(v) for k, v in value.items()}
    return value


def _row_to_record(row) -> dict:
    record = {key: _json_safe_value(value) for key, value in dict(row).items()}
    if record.get("documents"):
        record["documents"] = [
            {k: _json_safe_value(v) for k, v in dict(doc).items()}
            for doc in record["documents"]
        ]
    return record


def _write_documents(record: dict, directory: str) -> int:
    """Lay the stored extracted_text back out as the .txt files the processing
    stage expects, so triage and summarisation see exactly what they saw on the
    original run."""
    written = 0
    for index, doc in enumerate(record.get("documents") or []):
        text = doc.get("extracted_text")
        if not text or not text.strip():
            continue
        stem = os.path.splitext(os.path.basename(doc.get("file_name") or ""))[0]
        safe = "".join(c for c in stem if c.isalnum() or c in " ._-").strip() or "doc"
        # Index-prefixed because two stored documents can reduce to the same
        # name ("Report.pdf" and "Report.docx"), and the second would otherwise
        # overwrite the first and silently drop a document from the context.
        with open(os.path.join(directory, f"{index:03d}_{safe}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        written += 1
    return written


def _reprocess_one(position, total, record, temp_dir, summariser, determine_relevance):
    """Re-run the AI layer for one stored tender and write back only those fields."""
    gather_relevant_documents, build_tender_context, summarise_tender = summariser
    tender_ref = record.get("source_reference_id") or record.get("tender_id")
    logger.info(f"---- reprocess {position}/{total}: {tender_ref} ----")

    # upsert_tender finds the row to replace by (source_id, source_reference_id)
    # or (source_id, source_url). A row that matches neither would be INSERTED
    # under a new tender_id, duplicating the tender we just read -- so bail out
    # before spending anything on the AI calls.
    if not record.get("source_id") or not (
        record.get("source_reference_id") or record.get("source_url")
    ):
        logger.warning(f"{tender_ref}: no stable identity to match on, skipping")
        return "skipped"

    tender_dir = os.path.join(temp_dir, "reprocess")
    shutil.rmtree(tender_dir, ignore_errors=True)
    os.makedirs(tender_dir, exist_ok=True)

    if _write_documents(record, tender_dir) == 0:
        # Nothing stored to work from. Overwriting the summary with whatever the
        # model makes of an empty prompt would be worse than leaving it alone.
        logger.warning(f"{tender_ref}: no stored document text, skipping")
        return "skipped"

    try:
        relevant = gather_relevant_documents(tender_dir)
        summary = summarise_tender(build_tender_context(relevant))
        record["description"] = summary.description
        record["summary_headline"] = summary.headline
    except Exception as e:
        logger.error(f"{tender_ref}: summarisation failed: {e}")
        return "failed"

    try:
        record = determine_relevance(record)
    except Exception as e:
        logger.error(f"{tender_ref}: relevance determination failed: {e}")

    # location_states is not an AI field, but it can be re-derived here without
    # the landing page: the stored location text names the states outright.
    # Unioned, never replaced, so a value the extractor got from page text we no
    # longer have can only ever gain states, not lose them.
    from processing.tender_processor import resolve_location_states, states_from_location_text

    derived = states_from_location_text(record.get("location"))
    if derived:
        record["location_states"] = resolve_location_states(
            None, list(record.get("location_states") or []) + derived
        )

    title = record.get("title") or ""
    try:
        record["embedding"] = generate_embedding(
            f"Title: {title}. Summary: {record.get('description') or ''}".strip()
        )
    except Exception as e:
        # The summary is already paid for; keep the stored embedding rather than
        # throwing the whole tender away. Search stays on the old vector until
        # the next run.
        logger.warning(f"{tender_ref}: embedding failed, keeping the existing one: {e}")

    try:
        result = upsert_tender(bq_client, record)
    except Exception as e:
        logger.error(f"{tender_ref}: BigQuery upsert failed: {e}")
        return "failed"

    if result.get("action") == "inserted":
        # The identity guard above should make this impossible. If it happens,
        # a duplicate row now exists and someone needs to look.
        logger.error(
            f"{tender_ref}: reprocess INSERTED a new row (id={result['tender_id']}) "
            "instead of updating -- this is a duplicate, investigate"
        )

    logger.info(
        f"{tender_ref}: {result['action']} (fit={record.get('fit')}, "
        f"focus_areas={record.get('focus_areas')})"
    )
    return "reprocessed"


def reprocess_all():
    """Re-apply the AI layer to every tender already in BigQuery."""
    logger.info("Starting REPROCESS run (no scraping)")

    with tempfile.TemporaryDirectory() as temp_dir:
        # Same contract as the scrape path: select the runtime CFG before
        # importing anything that reads it at import time.
        runtime = prepare_runtime_config(temp_dir)
        if runtime.active:
            logger.info(f"Runtime tender processor configuration active: {runtime.path}")
        else:
            logger.warning(
                f"Using repository tender processor configuration fallback: {runtime.reason}"
            )

        summariser = _load_summariser()
        determine_relevance = _load_determine_relevance()

        # 0 = every tender. Cap it for a smoke test without touching the job
        # definition, the same way SCRAPE_LIMIT_CLOUD works:
        #   gcloud run jobs execute tender-batch-job --region australia-southeast1 \
        #     --update-env-vars PIPELINE_MODE=reprocess,REPROCESS_LIMIT=2 --wait
        try:
            limit = max(0, int(os.environ.get("REPROCESS_LIMIT", "0")))
        except ValueError:
            limit = 0
        limit_clause = f" LIMIT {limit}" if limit else ""

        count_job = bq_client.query(f"SELECT COUNT(*) AS n FROM `{TENDERS_TABLE}`")
        total = next(iter(count_job.result())).n
        if limit:
            total = min(total, limit)
            logger.warning(f"REPROCESS_LIMIT={limit}: only the first {total} tender(s) will run")
        _stage(1, 1, f"REPROCESS {total} tender(s) already in BigQuery")

        # Streamed a few rows at a time, never collected into a list: each row
        # carries the full extracted text of every attachment, so materialising
        # the table would exhaust the job exactly like an unfreed attachment.
        select_cols = ", ".join(ALL_COLUMNS)
        rows = bq_client.query(
            f"SELECT {select_cols} FROM `{TENDERS_TABLE}`{limit_clause}"
        ).result(page_size=5)

        started_at = datetime.now(timezone.utc).isoformat()
        counts = {"reprocessed": 0, "skipped": 0, "failed": 0}
        # Published before the first tender so the admin page shows the run as
        # live immediately, rather than only once one has finished.
        publish_reprocess_progress({
            "state": "running", "processed": 0, "total": total,
            "started_at": started_at, **counts,
        })

        for position, row in enumerate(rows, start=1):
            try:
                outcome = _reprocess_one(
                    position, total, _row_to_record(row), temp_dir,
                    summariser, determine_relevance,
                )
            except Exception as e:
                logger.error(f"Unexpected failure reprocessing row {position}: {e}")
                outcome = "failed"
            counts[outcome] += 1
            publish_reprocess_progress({
                "state": "running", "processed": position, "total": total,
                "started_at": started_at, **counts,
            })
            _free_memory()

    publish_reprocess_progress({
        "state": "finished", "processed": sum(counts.values()), "total": total,
        "started_at": started_at, **counts,
    })
    logger.info(
        f"Reprocess finished: {counts['reprocessed']} reprocessed, "
        f"{counts['skipped']} skipped (no stored text), {counts['failed']} failed"
    )
    return counts


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
    # Reprocess-only run, triggered from the admin config page. Set as an
    # execution override, so the scheduled daily run is unaffected.
    if os.environ.get("PIPELINE_MODE", "").strip().lower() == "reprocess" \
            or "--reprocess" in sys.argv[1:]:
        reprocess_all()
        return

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
    previous_health = _load_previous_health_records()

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
            prev_success = previous_health.get(source_id.lower())
            # 1. Run the web scraper. It downloads the tender page text and
            # attachments into temp_dir.
            _stage(1, 3, f"SCRAPE {source_id}")
            scraped, failure, health_record, summary_row = _scrape_one(
                source_id, scrape, temp_dir, SCRAPE_LIMIT_CLOUD, True, now_iso, prev_success=prev_success
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
                # 2. Store attachments, then hand each tender to AI processing.
                # The scrapers have already written each attachment's text
                # (error_scrapers/common.py), so there is no separate extraction step.
                _stage(2, 3, f"STORE attachments, AI-process, upsert "
                             f"{source_id} ({len(tender_folders)} tender(s))")
                logger.info("🤖 Preparing data for AI Processing...")
                errors = _process_tenders(
                    temp_dir, tender_folders, scraped, process_tender, determine_relevance
                )
                if errors:
                    failures.append(
                        (source_id, f"{errors} tender(s) hit an unexpected error while processing")
                    )
                health_records[-1] = _refresh_health(
                    health_records[-1], summary_rows[-1], prev_success=prev_success
                )
                summary_rows[-1] = _refresh_summary_row(health_records[-1], summary_rows[-1])
                publish_health_status_to_gcs(
                    health_records, bucket_name="tenderai-dev-documents"
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
    _stage(3, 3, "FINISH")
    logger.info("Pipeline finished. Temporary files wiped from memory.")
    if failures:
        summary = ", ".join(f"{s} ({why})" for s, why in failures)
        logger.error(f"Pipeline finished with scraper failures: {summary}")

        #send alert email with gcs alerts that detect the previous error and email admins


        os._exit(1)
    os._exit(0)


if __name__ == "__main__":
    main()
