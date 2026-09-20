from fastapi import APIRouter, Depends

from app.auth import current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def me(user: dict = Depends(current_user)) -> dict:
    """The caller's profile, as the API sees it.

    The frontend calls this right after sign-in: for a tenant member arriving
    through Entra SSO for the first time it is what creates their Firestore
    profile, and its `isAdmin` is authoritative over the client-side read.
    """
    return user
