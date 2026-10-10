"""Admin-triggered pipeline runs.

The reprocess run re-applies the AI layer to tenders already in BigQuery. It is
long and costly, so it is not run inside the request: this starts the existing
Cloud Run job with a PIPELINE_MODE=reprocess override and returns immediately.
The override applies to that execution only, so the scheduled daily run keeps
scraping as normal.
"""

from fastapi import APIRouter, Depends, HTTPException
from google.api_core import exceptions as google_exceptions
from google.auth import exceptions as google_auth_exceptions
from google.cloud import run_v2

from app.auth import require_admin
from app.config import settings
from app.models import ReprocessRunResponse


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
