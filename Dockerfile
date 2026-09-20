# Container for run_pipeline.py: scrape -> validate -> submit to BigQuery,
# on a schedule (Cloud Scheduler -> Cloud Run Job). This does NOT build
# manager.py or anything AI-related (processing/tender_processor.py) --
# that is a separate, still-in-progress pipeline. This image only automates
# the scrape/validate/submit path that already works end to end.
#
# Build (no local Docker needed -- builds in the cloud):
#   gcloud builds submit --tag=<region>-docker.pkg.dev/tenderai-dev/tenderai/run-pipeline .
#
# Includes Chrome so vic/qld (which need a real browser) run too, not just
# the plain-HTTP sources (austender/wa/nt). Adds ~1-1.5GB to the image and
# needs ~4Gi memory at runtime for the browser sources -- see
# web_scrapers/INTEGRATION.md's "pipeline image needs Chrome" section. Cost
# impact is small since Cloud Run Jobs only bill for actual run time, not
# while idle between scheduled runs.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
# Tells the scrapers to apply container-specific Chrome flags (--no-sandbox,
# /dev/shm off) and lets run_scrapers.py start/stop its own virtual display.
ENV RUNNING_IN_CONTAINER=1

WORKDIR /app

# --- Chrome layer, copied from web_scrapers/Dockerfile (see its comments for
# why Chrome specifically, not Chromium, and why headed via Xvfb not headless)
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

# Both requirement files, into the one environment this image runs with.
COPY ingestion/requirements.txt ./ingestion/requirements.txt
COPY web_scrapers/requirements.txt ./web_scrapers/requirements.txt
RUN pip install --no-cache-dir \
        -r ingestion/requirements.txt \
        -r web_scrapers/requirements.txt

COPY ingestion/ ./ingestion/
COPY web_scrapers/ ./web_scrapers/
COPY run_pipeline.py ./run_pipeline.py

# Sensible defaults; every one is overridable per-job without rebuilding the
# image (see run_pipeline.py's parse_args -- each flag reads its env var
# first). LIMIT=0 means no cap: a scheduled daily run should not silently
# drop tenders past an arbitrary count picked for local testing.
ENV SOURCES=austender,wa,nt,vic,qld \
    LIMIT=0

ENTRYPOINT ["python", "run_pipeline.py"]
