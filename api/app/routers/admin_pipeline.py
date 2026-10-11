"""Admin-triggered pipeline runs.

The reprocess run re-applies the AI layer to tenders already in BigQuery. It is
long and costly, so it is not run inside the request: this starts the existing
Cloud Run job with a PIPELINE_MODE=reprocess override and returns immediately.
The override applies to that execution only, so the scheduled daily run keeps
scraping as normal.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from google.api_core import exceptions as google_exceptions
from google.auth import exceptions as google_auth_exceptions
from google.cloud import run_v2, storage

from app.auth import require_admin
from app.config import settings
from app.models import ReprocessRunResponse, ReprocessStatus

logger = logging.getLogger("AdminPipeline")


router = APIRouter(prefix="/admin/pipeline", tags=["admin pipeline"])


def _job_path() -> str:
    return (
        f"projects/{settings.google_cloud_project}"
        f"/locations/{settings.pipeline_job_region}"
        f"/jobs/{settings.pipeline_job_name}"
    )


@router.post("/reprocess", response_model=ReprocessRunResponse, status_code=202)
def start_reprocess(_admin: dict = Depends(require_admin)) -> ReprocessRunResponse:
    job_path = _job_path()

    try:
        executions_client = run_v2.ExecutionsClient()
        # Two concurrent runs would upsert the same rows and race on content
        # hashes, as well as doubling the Gemini bill.
        for execution in executions_client.list_executions(parent=job_path):
            if execution.running_count > 0:
                raise HTTPException(
                    status_code=409,
                    detail="A pipeline run is already in progress. Wait for it to finish.",
                )

        jobs_client = run_v2.JobsClient()
        operation = jobs_client.run_job(
            request=run_v2.RunJobRequest(
                name=job_path,
                overrides=run_v2.RunJobRequest.Overrides(
                    container_overrides=[
                        run_v2.RunJobRequest.Overrides.ContainerOverride(
                            env=[run_v2.EnvVar(name="PIPELINE_MODE", value="reprocess")]
                        )
                    ]
                ),
            )
        )
    except HTTPException:
        raise
    except google_exceptions.NotFound as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Pipeline job {settings.pipeline_job_name} was not found.",
        ) from exc
    except google_exceptions.PermissionDenied as exc:
        raise HTTPException(
            status_code=503,
            detail="This service is not permitted to start the pipeline job.",
        ) from exc
    except (
        google_exceptions.GoogleAPIError,
        google_auth_exceptions.GoogleAuthError,
    ) as exc:
        raise HTTPException(status_code=503, detail="Could not start the pipeline run.") from exc

    # The operation is the execution starting up; don't wait for the run itself,
    # which takes far longer than any request may.
    return ReprocessRunResponse(execution=operation.metadata.name if operation.metadata else "")


def _execution_running(job_path: str) -> bool:
    """Whether Cloud Run currently has an execution of the job in flight."""
    executions_client = run_v2.ExecutionsClient()
    return any(
        execution.running_count > 0
        for execution in executions_client.list_executions(parent=job_path)
    )


def _stored_progress() -> dict:
    """The progress file the run publishes, or {} if there isn't one yet."""
    try:
        client = storage.Client(project=settings.google_cloud_project)
        blob = client.bucket(settings.scraper_health_bucket).blob(
            settings.reprocess_progress_object
        )
        if not blob.exists():
            return {}
        payload = json.loads(blob.download_as_text())
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        # Progress is a nicety; the running flag is what locks the button.
        logger.warning("Could not read reprocess progress: %s", exc)
        return {}


@router.get("/reprocess/status", response_model=ReprocessStatus)
def reprocess_status(_admin: dict = Depends(require_admin)) -> ReprocessStatus:
    progress = _stored_progress()

    try:
        running = _execution_running(_job_path())
    except (
        google_exceptions.GoogleAPIError,
        google_auth_exceptions.GoogleAuthError,
    ) as exc:
        # Fall back to what the file claims rather than failing the poll, but
        # never report a finished run as still going.
        logger.warning("Could not read Cloud Run execution state: %s", exc)
        running = progress.get("state") == "running"

    def _int(key: str) -> int | None:
        value = progress.get(key)
        return value if isinstance(value, int) else None

    return ReprocessStatus(
        running=running,
        state=progress.get("state") if isinstance(progress.get("state"), str) else None,
        processed=_int("processed"),
        total=_int("total"),
        reprocessed=_int("reprocessed"),
        skipped=_int("skipped"),
        failed=_int("failed"),
        started_at=progress.get("started_at") if isinstance(progress.get("started_at"), str) else None,
        updated_at=progress.get("updated_at") if isinstance(progress.get("updated_at"), str) else None,
    )
