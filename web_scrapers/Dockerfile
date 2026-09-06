# Container for the scraper stage, run as a Cloud Run job.
#
# Build from the repo root so the whole web_scrapers/ package is in context:
#   docker build -f web_scrapers/Dockerfile -t <image> .
#
# Ships Google Chrome because two of the three portals (VIC, QLD) render their
# search results client-side and sit behind Cloudflare, so they cannot be
# scraped over plain HTTP. AusTender alone would not need any of this.
FROM python:3.12-slim

# Unbuffered so Cloud Logging gets the scrape progress as it happens, not in
# one lump when the task exits.
ENV PYTHONUNBUFFERED=1

# Tells the scrapers to apply the container-specific Chrome flags
# (--no-sandbox, shared memory off /dev/shm). See common.build_uc_driver.
ENV RUNNING_IN_CONTAINER=1

WORKDIR /app

# Google Chrome (not Chromium): SeleniumBase's UC mode is built and tested
# against Chrome, and its anti-bot patches assume Chrome's exact fingerprint.
# xvfb gives us a virtual display so Chrome can run headed -- Cloudflare
# challenges true headless Chrome far more aggressively.
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

COPY web_scrapers/requirements.txt ./web_scrapers/requirements.txt
RUN pip install --no-cache-dir -r web_scrapers/requirements.txt

COPY web_scrapers/ ./web_scrapers/

# SeleniumBase writes its downloaded chromedriver and its lock files next to
# the package, so the runtime user needs somewhere writable.
ENV HOME=/tmp

# Overridable at deploy time: SOURCES, LIMIT, OUTPUT_BUCKET, OUTPUT_PREFIX.
ENV SOURCES=austender \
    LIMIT=10 \
    OUTPUT_PREFIX=raw

# The virtual display is started from Python (see run_scrapers.virtual_display)
# rather than by wrapping this in xvfb-run, so a display failure is logged and
# degrades to headless instead of hanging the job with no output.
CMD ["python", "-m", "web_scrapers.run_scrapers"]
