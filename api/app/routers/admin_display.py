"""Shared admin display settings.

Two view toggles that apply to every admin rather than to one browser, so they
live in Firestore rather than localStorage. They affect presentation only --
which tenders an admin is shown, and whether the raw record is printed on the
card. What a non-admin can see is decided server-side by min_visible_fit, not
by anything here.

Kept out of the GCS tender_processor.cfg deliberately: that file is the
pipeline's input, and a display preference has no business bumping its
generation or being read by a batch job.
"""

from fastapi import APIRouter, Depends, HTTPException
from google.api_core import exceptions as google_exceptions

from app.auth import require_admin
from app.models import DisplaySettings

router = APIRouter(prefix="/admin/settings", tags=["admin settings"])

_COLLECTION = "settings"
_DOCUMENT = "display"
_DEFAULTS = {"show_json": False, "show_irrelevant": False}


def _doc():
    # Imported here rather than at module import so the module can be loaded
    # without Firebase being initialised (the test suite does exactly that).
    from app.auth import _db

    return _db.collection(_COLLECTION).document(_DOCUMENT)


@router.get("/display", response_model=DisplaySettings)
def get_display_settings(_admin: dict = Depends(require_admin)) -> DisplaySettings:
    try:
        snap = _doc().get()
    except google_exceptions.GoogleAPIError as exc:
        raise HTTPException(status_code=503, detail="Could not read display settings.") from exc

    stored = snap.to_dict() if snap.exists else {}
    # Unknown keys in the stored document are ignored rather than rejected, so
    # a setting removed in a later version can't break the page.
    return DisplaySettings(**{**_DEFAULTS, **{k: v for k, v in (stored or {}).items() if k in _DEFAULTS}})


@router.patch("/display", response_model=DisplaySettings)
def patch_display_settings(
    update: DisplaySettings,
    _admin: dict = Depends(require_admin),
) -> DisplaySettings:
    values = update.model_dump()
    try:
        _doc().set(values, merge=True)
    except google_exceptions.GoogleAPIError as exc:
        raise HTTPException(status_code=503, detail="Could not save display settings.") from exc
    return DisplaySettings(**values)
