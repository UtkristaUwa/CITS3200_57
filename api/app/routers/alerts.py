from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_admin
from app.teams import (
    build_test_adaptive_card,
    get_teams_config,
    save_teams_config,
    send_teams_card,
)

router = APIRouter(prefix="/admin/alerts", tags=["admin alerts"])


class TeamsConfigResponse(BaseModel):
    enabled: bool
    webhook_url: str
    min_fit_score: int = Field(ge=0, le=100, default=70)
    updated_at: str | None = None


class TeamsConfigUpdate(BaseModel):
    enabled: bool
    webhook_url: str
    min_fit_score: int = Field(ge=0, le=100, default=70)


class TeamsTestRequest(BaseModel):
    webhook_url: str | None = None


class TeamsTestResponse(BaseModel):
    success: bool
    message: str


@router.get("/teams", response_model=TeamsConfigResponse)
def get_teams_alert_config(_admin: dict = Depends(require_admin)) -> TeamsConfigResponse:
    config = get_teams_config()
    return TeamsConfigResponse(**config)


@router.put("/teams", response_model=TeamsConfigResponse)
def update_teams_alert_config(
    body: TeamsConfigUpdate,
    _admin: dict = Depends(require_admin),
) -> TeamsConfigResponse:
    try:
        saved = save_teams_config(
            enabled=body.enabled,
            webhook_url=body.webhook_url,
            min_fit_score=body.min_fit_score,
        )
        return TeamsConfigResponse(**saved)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/teams/test", response_model=TeamsTestResponse)
def test_teams_alert_webhook(
    body: TeamsTestRequest,
    _admin: dict = Depends(require_admin),
) -> TeamsTestResponse:
    target_url = (body.webhook_url or "").strip()
    if not target_url:
        current_cfg = get_teams_config()
        target_url = current_cfg.get("webhook_url", "").strip()

    if not target_url:
        raise HTTPException(
            status_code=400,
            detail="No webhook URL provided or configured.",
        )

    test_card = build_test_adaptive_card()
    success, message = send_teams_card(target_url, test_card)

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return TeamsTestResponse(success=True, message=message)
