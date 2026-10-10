import json
import logging
from fastapi import APIRouter
from google.cloud import storage

from app.config import settings
from app.models import HealthOut, ScraperHealthRecord

logger = logging.getLogger("api.health")

router = APIRouter()


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok")


@router.get("/health/scrapers", response_model=list[ScraperHealthRecord])
@router.get("/admin/health/scrapers", response_model=list[ScraperHealthRecord])
def scraper_health() -> list[ScraperHealthRecord]:
    if settings.use_mock_data:
        return [
            ScraperHealthRecord(
                website="GRANTCONNECT",
                url="https://www.grants.gov.au",
                last_run="2026-10-06T21:00:43.303435+00:00",
                status="Success",
                status_color="success",
                message="Scraping was a total success.",
            ),
            ScraperHealthRecord(
                website="BUYNSW",
                url="https://buy.nsw.gov.au",
                last_run="2026-10-06T21:00:43.303435+00:00",
                status="Failed to download",
                status_color="warning",
                message="Partial tender information gathered; requires manual verification.",
            ),
            ScraperHealthRecord(
                website="TENDERS_ACT",
                url="https://www.tenders.act.gov.au",
                last_run="2026-10-06T21:00:43.303435+00:00",
                status="Error",
                status_color="error",
                message="Anti-bot detections (Cloudflare/reCAPTCHA) have blocked access.",
            ),
        ]

    try:
        client = storage.Client(project=settings.google_cloud_project)
        bucket = client.bucket(settings.scraper_health_bucket)
        blob = bucket.blob(settings.scraper_health_object)
        if not blob.exists():
            logger.warning(
                "Scraper health blob gs://%s/%s does not exist",
                settings.scraper_health_bucket,
                settings.scraper_health_object,
            )
            return []
        content = blob.download_as_text()
        records = json.loads(content)
        if not isinstance(records, list):
            logger.warning("Scraper health JSON is not a list: %r", content[:200])
            return []
        return [ScraperHealthRecord(**record) for record in records]
    except Exception as exc:
        logger.error("Failed to read scraper health from Cloud Storage: %s", exc)
        return []

