from fastapi import APIRouter, Depends, HTTPException

from app import runtime_config
from app.auth import require_admin
from app.models import (
    ExtractionPromptResponse,
    ExtractionPromptUpdate,
    ModelConfigResponse,
    ModelConfigUpdate,
    RelevanceConfigResponse,
    RelevanceConfigUpdate,
)


router = APIRouter(prefix="/admin/config", tags=["admin configuration"])


def _response(config: runtime_config.RuntimeModelConfig) -> ModelConfigResponse:
    return ModelConfigResponse(
        triage_model=config.triage_model,
        extraction_model=config.extraction_model,
        generation=config.generation,
    )


def _relevance_response(config: runtime_config.RuntimeRelevanceConfig) -> RelevanceConfigResponse:
    return RelevanceConfigResponse(**config.__dict__)


def _raise_http_error(exc: runtime_config.RuntimeConfigError) -> None:
    if isinstance(exc, runtime_config.RuntimeConfigConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, runtime_config.RuntimeConfigInvalidUpdate):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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


@router.get("/prompts", response_model=ExtractionPromptResponse)
def get_prompts(_admin: dict = Depends(require_admin)) -> ExtractionPromptResponse:
    try:
        config = runtime_config.get_extraction_prompt()
    except runtime_config.RuntimeConfigError as exc:
        _raise_http_error(exc)
    return ExtractionPromptResponse(**config.__dict__)


@router.patch("/prompts", response_model=ExtractionPromptResponse)
def patch_prompts(
    update: ExtractionPromptUpdate,
    _admin: dict = Depends(require_admin),
) -> ExtractionPromptResponse:
    try:
        config = runtime_config.update_extraction_prompt(
            expected_generation=update.generation,
            field_extraction=update.field_extraction,
        )
    except runtime_config.RuntimeConfigError as exc:
        _raise_http_error(exc)
    return ExtractionPromptResponse(**config.__dict__)


@router.get("/relevance", response_model=RelevanceConfigResponse)
def get_relevance(_admin: dict = Depends(require_admin)) -> RelevanceConfigResponse:
    try:
        return _relevance_response(runtime_config.get_relevance_config())
    except runtime_config.RuntimeConfigError as exc:
        _raise_http_error(exc)


@router.patch("/relevance", response_model=RelevanceConfigResponse)
def patch_relevance(
    update: RelevanceConfigUpdate,
    _admin: dict = Depends(require_admin),
) -> RelevanceConfigResponse:
    try:
        values = update.model_dump(exclude={"generation"}, exclude_none=True)
        return _relevance_response(
            runtime_config.update_relevance_config(
                expected_generation=update.generation,
                **values,
            )
        )
    except runtime_config.RuntimeConfigError as exc:
        _raise_http_error(exc)
