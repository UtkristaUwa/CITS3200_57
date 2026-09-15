# NOT DEPLOYED. This image is not a service, not a job, and nothing schedules
# it. The pipeline runs from the repo-root Dockerfile, which builds manager.py;
# the scrapers are a library it imports.
#
# What this file is for: the two browser-driven portals (VIC, QLD) need a real
# Chrome, and the exact set of packages and flags that make Chrome work in a
# container took a while to get right. Keeping it here means the requirement
# stays executable -- you can build this and prove the browser path still works
# without deploying anything:
#
#   docker build -f web_scrapers/Dockerfile -t scrapers-check .
#   docker run --rm scrapers-check --sources vic --limit 2
#
# The pipeline image needs the same Chrome layer or it can only run austender.
# See INTEGRATION.md; the block between the two markers below is the part to
# copy across.
FROM python:3.12-slim

# Unbuffered so container logs get the scrape progress as it happens, not in
# one lump when the process exits.
ENV PYTHONUNBUFFERED=1

# Tells the scrapers to apply the container-specific Chrome flags
# (--no-sandbox, shared memory off /dev/shm). See common.build_uc_driver.
ENV RUNNING_IN_CONTAINER=1

WORKDIR /app

# --- 8< --- chrome layer: copy this into the pipeline image --- 8< ---
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

# SeleniumBase writes its downloaded chromedriver and its lock files next to
# the package, so the runtime user needs somewhere writable.
ENV HOME=/tmp
# --- >8 --- end of the chrome layer --- >8 ---

COPY web_scrapers/requirements.txt ./web_scrapers/requirements.txt
RUN pip install --no-cache-dir -r web_scrapers/requirements.txt

COPY web_scrapers/ ./web_scrapers/

ENV SOURCES=austender \
    LIMIT=10

ENTRYPOINT ["python", "-m", "web_scrapers.run_scrapers"]
