from fastapi import APIRouter, Depends, HTTPException

from app import runtime_config
from app.auth import require_admin
from app.models import ModelConfigResponse, ModelConfigUpdate


router = APIRouter(prefix="/admin/config", tags=["admin configuration"])


def _response(config: runtime_config.RuntimeModelConfig) -> ModelConfigResponse:
    return ModelConfigResponse(
        triage_model=config.triage_model,
        extraction_model=config.extraction_model,
        generation=config.generation,
    )


def _raise_http_error(exc: runtime_config.RuntimeConfigError) -> None:
    if isinstance(exc, runtime_config.RuntimeConfigConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/models", response_model=ModelConfigResponse)
def get_models(_admin: dict = Depends(require_admin)) -> ModelConfigResponse:
    try:
        return _response(runtime_config.get_model_config())
    except runtime_config.RuntimeConfigError as exc:
        _raise_http_error(exc)


@router.patch("/models", response_model=ModelConfigResponse)
def patch_models(
    update: ModelConfigUpdate,
    _admin: dict = Depends(require_admin),
) -> ModelConfigResponse:
    try:
        return _response(
            runtime_config.update_model_config(
                expected_generation=update.generation,
                triage_model=update.triage_model,
                extraction_model=update.extraction_model,
            )
        )
    except runtime_config.RuntimeConfigError as exc:
        _raise_http_error(exc)
