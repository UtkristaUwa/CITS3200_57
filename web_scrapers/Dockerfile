# Container for the scraper stage, run as a Cloud Run job.
#
# Build from the repo root so the whole web_scrapers/ package is in context:
#   docker build -f web_scrapers/Dockerfile -t <image> .
FROM python:3.12-slim

# Unbuffered so Cloud Logging gets the scrape progress as it happens, not in
# one lump when the task exits.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY web_scrapers/requirements.txt ./web_scrapers/requirements.txt
RUN pip install --no-cache-dir -r web_scrapers/requirements.txt

COPY web_scrapers/ ./web_scrapers/

# Overridable at deploy time: SOURCES, LIMIT, OUTPUT_BUCKET, OUTPUT_PREFIX.
ENV SOURCES=austender \
    LIMIT=10 \
    OUTPUT_PREFIX=raw

CMD ["python", "-m", "web_scrapers.run_scrapers"]
