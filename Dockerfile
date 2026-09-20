# Container for manager.py: full daily pipeline -- scrape → document extract
# → attachment upload → Gemini AI processing → BigQuery upsert.
# Triggered on a schedule (Cloud Scheduler → Cloud Run Job tender-batch-job)
# at 5 am AWST (UTC+8) every day (cron: 0 5 * * *, tz: Australia/Perth).
#
# Build (no local Docker needed -- builds in the cloud):
#   gcloud builds submit --config cloudbuild.yaml .
#
# Includes Chrome so tenders_act (which needs a real browser) runs too.
# Needs ~4 Gi memory at runtime for the browser source.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
# Tells scrapers to apply container-specific Chrome flags (--no-sandbox,
# /dev/shm off) and lets run_scrapers.py start/stop its own virtual display.
ENV RUNNING_IN_CONTAINER=1

WORKDIR /app

# --- Chrome layer (required by error_scrapers/tenders_act) ---
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg xvfb xauth \
        fonts-liberation fonts-dejavu-core \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

# SeleniumBase writes its chromedriver + lock files next to the package, so
# the runtime user (Cloud Run runs as non-root) needs a writable HOME.
ENV HOME=/tmp
# --- end Chrome layer ---

# Install all Python dependencies.
COPY requirements.txt ./requirements.txt
COPY ingestion/requirements.txt ./ingestion/requirements.txt
COPY web_scrapers/requirements.txt ./web_scrapers/requirements.txt
COPY document_scraper/requirements.txt ./document_scraper/requirements.txt
RUN pip install --no-cache-dir \
        -r requirements.txt \
        -r ingestion/requirements.txt \
        -r web_scrapers/requirements.txt \
        -r document_scraper/requirements.txt

# Copy all source modules that manager.py imports.
COPY ingestion/ ./ingestion/
COPY web_scrapers/ ./web_scrapers/
COPY error_scrapers/ ./error_scrapers/
COPY document_scraper/ ./document_scraper/
COPY processing/ ./processing/
COPY attachment_store.py ./attachment_store.py
COPY manager.py ./manager.py

# SCRAPE_LIMIT=0 means no cap -- the scheduled daily run should not silently
# drop tenders past an arbitrary count picked for local testing.
# Override per-job via --update-env-vars without rebuilding the image.
ENV SCRAPE_LIMIT=10

ENTRYPOINT ["python", "manager.py"]
