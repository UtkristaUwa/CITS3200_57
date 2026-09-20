"""Request authentication for the TenderAI API.

Every request carries a Firebase ID token in `Authorization: Bearer <token>`.
Two sign-in methods are accepted:

* `microsoft.com` — Entra ID SSO. The Entra app registration is single-tenant,
  so Microsoft only ever issues a token to a member of the client's tenant;
  that tenant boundary is the access control. A tenant member who has never
  signed in before is auto-provisioned here as an ordinary (non-admin) user,
  because their Firebase uid does not exist until this first sign-in and so
  nobody could have pre-created their profile doc.
* `password` — the invite flow in `functions/src/index.ts`, which creates
  `users/{uid}` up front. Those users are never auto-provisioned: no doc, no
  access.

Admin rights are never granted automatically by either path. `isAdmin` is set
either at invite time (`inviteUser`) or afterwards by an existing admin through
the `setUserAdmin` callable, which the Users page drives. Both run on the Admin
SDK; firestore.rules makes the field unwritable from the browser, so there is no
third way in.
"""

import firebase_admin
from fastapi import Depends, HTTPException, Request
from firebase_admin import auth as fb_auth, credentials, firestore

from app.config import settings

if not firebase_admin._apps:
    firebase_admin.initialize_app(
        credentials.ApplicationDefault(),
        {"projectId": settings.google_cloud_project},
    )

_db = firestore.client()

MICROSOFT_PROVIDER = "microsoft.com"
PASSWORD_PROVIDER = "password"
ALLOWED_PROVIDERS = {MICROSOFT_PROVIDER, PASSWORD_PROVIDER}

# `inviteUser` writes status 'pending' and the frontend flips it to 'active' on
# first login, so 'pending' is a live account, not a blocked one. Only these
# statuses — and an explicit active=False — lock someone out.
BLOCKED_STATUSES = {"disabled", "suspended", "revoked"}


def _is_active(profile: dict) -> bool:
    if profile.get("active") is False:
        return False
    return str(profile.get("status", "")).lower() not in BLOCKED_STATUSES


def _domain_allowed(email: str | None) -> bool:
    """Defence in depth behind the Entra tenant boundary.

    Leave ALLOWED_EMAIL_DOMAINS empty to accept whatever the tenant issues.
    """
    allowed = settings.allowed_email_domains_list
    if not allowed:
        return True
    if not email or "@" not in email:
        return False
    return email.rsplit("@", 1)[1].lower() in allowed


def _promote_pending(uid: str, email: str | None) -> dict | None:
    """Turn a `pending_users` entry keyed by email into a `users/{uid}` doc.

    Kept for invites addressed to someone whose uid isn't known yet. The
    current Cloud Function writes `users/{uid}` directly instead, so this is a
    no-op for those; it costs one Firestore read on a first sign-in only.
    """
    if not email:
        return None
    key = email.strip().lower()
    pending_ref = _db.collection("pending_users").document(key)
    pending = pending_ref.get()
    if not pending.exists:
        return None

    profile = {
        "email": key,
        "active": True,
        "status": "active",
        "isAdmin": bool(pending.to_dict().get("isAdmin", False)),
    }
    _db.collection("users").document(uid).set(
        {**profile, "createdAt": firestore.SERVER_TIMESTAMP},
        merge=True,
    )
    pending_ref.delete()
    return profile


def _auto_provision_sso(uid: str, email: str | None) -> dict | None:
    """Create a profile for a tenant member signing in with Entra for the first time."""
    if not settings.auto_provision_sso:
        return None

    profile = {
        "email": (email or "").strip().lower() or None,
        "active": True,
        "status": "active",
        "isAdmin": False,
        "provider": MICROSOFT_PROVIDER,
        "autoProvisioned": True,
    }
    _db.collection("users").document(uid).set(
        {**profile, "createdAt": firestore.SERVER_TIMESTAMP},
        merge=True,
    )
    return profile


async def current_user(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")

    try:
        claims = fb_auth.verify_id_token(header[7:])
    except Exception:
        # Deliberately opaque: an expired token and a forged one look the same
        # to the caller. The frontend refreshes and retries once on a 401.
        raise HTTPException(401, "invalid token")

    provider = claims.get("firebase", {}).get("sign_in_provider")
    if provider not in ALLOWED_PROVIDERS:
        raise HTTPException(403, "unsupported sign-in method")

    uid = claims["uid"]
    email = claims.get("email")

    if provider == MICROSOFT_PROVIDER and not _domain_allowed(email):
        raise HTTPException(403, "email domain not permitted")

    snap = _db.collection("users").document(uid).get()
    if snap.exists:
        profile = snap.to_dict() or {}
    else:
        profile = _promote_pending(uid, email)
        if profile is None and provider == MICROSOFT_PROVIDER:
            profile = _auto_provision_sso(uid, email)

    if not profile or not _is_active(profile):
        raise HTTPException(403, "not authorised for this application")

    return {
        "uid": uid,
        "email": email,
        "provider": provider,
        "isAdmin": bool(profile.get("isAdmin", False)),
        "status": profile.get("status", "active"),
    }


async def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user.get("isAdmin"):
        raise HTTPException(403, "admin only")
    return user
