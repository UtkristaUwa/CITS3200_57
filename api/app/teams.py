"""Microsoft Teams Alerts Integration Service for TenderAI.

Handles:
1. Building Adaptive Cards for tender alerts (single tender, digest, test).
2. Dispatching HTTP POST payloads to Microsoft Teams incoming webhook URLs.
3. Reading and persisting Teams alert configuration in Firestore (system_config/alerts).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("TeamsAlerts")

DEFAULT_MIN_FIT_SCORE = 70
DEFAULT_PORTAL_URL = "https://tenderai-dev-f0283.web.app"
FIRESTORE_CONFIG_COLLECTION = "system_config"
FIRESTORE_CONFIG_DOC = "alerts"


def _get_firestore_client():
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        project_id = "tenderai-dev"
        try:
            from app.config import settings
            project_id = settings.google_cloud_project
        except Exception:
            project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "tenderai-dev")

        if not firebase_admin._apps:
            firebase_admin.initialize_app(
                credentials.ApplicationDefault(),
                {"projectId": project_id},
            )
        return firestore.client()
    except Exception as exc:
        logger.warning("Firestore client unavailable in teams module: %s", exc)
        return None


def get_teams_config() -> dict[str, Any]:
    """Retrieve the current Microsoft Teams alerts configuration.

    Reads from Firestore system_config/alerts with fallback to environment variables.
    """
    db = _get_firestore_client()
    if db:
        try:
            doc = db.collection(FIRESTORE_CONFIG_COLLECTION).document(FIRESTORE_CONFIG_DOC).get()
            if doc.exists:
                data = doc.to_dict() or {}
                return {
                    "enabled": bool(data.get("enabled", False)),
                    "webhook_url": str(data.get("webhook_url", "")),
                    "min_fit_score": int(data.get("min_fit_score", DEFAULT_MIN_FIT_SCORE)),
                    "updated_at": data.get("updated_at"),
                }
        except Exception as exc:
            logger.warning("Failed to fetch Teams config from Firestore: %s", exc)

    # Fallback to environment variables
    env_url = os.environ.get("TEAMS_WEBHOOK_URL", "")
    return {
        "enabled": bool(env_url),
        "webhook_url": env_url,
        "min_fit_score": int(os.environ.get("TEAMS_MIN_FIT_SCORE", DEFAULT_MIN_FIT_SCORE)),
        "updated_at": None,
    }


def save_teams_config(enabled: bool, webhook_url: str, min_fit_score: int = DEFAULT_MIN_FIT_SCORE) -> dict[str, Any]:
    """Persist Teams alerts configuration into Firestore system_config/alerts."""
    db = _get_firestore_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    config_data = {
        "enabled": enabled,
        "webhook_url": (webhook_url or "").strip(),
        "min_fit_score": max(0, min(100, min_fit_score)),
        "updated_at": now_iso,
    }

    if db:
        try:
            db.collection(FIRESTORE_CONFIG_COLLECTION).document(FIRESTORE_CONFIG_DOC).set(
                config_data, merge=True
            )
        except Exception as exc:
            logger.error("Failed to save Teams config to Firestore: %s", exc)
            raise RuntimeError(f"Could not persist Teams configuration: {exc}")

    return config_data


def format_money(amount: float | None, notes: str | None = None) -> str:
    if amount is not None:
        try:
            return f"${amount:,.0f} AUD"
        except Exception:
            return f"${amount} AUD"
    if notes:
        return notes
    return "Not disclosed"


def build_tender_card(tender: dict, portal_base_url: str = DEFAULT_PORTAL_URL) -> dict:
    """Builds a single-tender Microsoft Teams Adaptive Card.

    Displays:
    - Title
    - AI Headline
    - Metadata facts (Agency, Location, Closing Date, Est. Value)
    - Buttons: 'Open in TenderAI' and 'Original Source'
    """
    title = (tender.get("title") or "Untitled Tender Opportunity").strip()[:250]
    headline = (tender.get("summary_headline") or tender.get("description") or "").strip()[:600]
    agency = str(tender.get("issuing_agency") or "Unknown Agency").strip()[:150]
    location = str(tender.get("location") or "Australia").strip()[:100]
    closing = str(tender.get("closing_date") or "Not specified")[:50]
    value_display = format_money(tender.get("value_amount"), tender.get("value_notes"))

    tender_id = tender.get("tender_id") or ""
    app_url = f"{portal_base_url}/?tender={tender_id}" if tender_id else portal_base_url
    raw_source_url = tender.get("source_url")
    valid_source_url = (
        raw_source_url
        if raw_source_url and str(raw_source_url).startswith(("http://", "https://"))
        else None
    )

    facts = [
        {"title": "Agency:", "value": agency},
        {"title": "Location:", "value": location},
        {"title": "Closing Date:", "value": closing},
        {"title": "Est. Value:", "value": value_display},
    ]

    body_elements: list[dict] = [
        {
            "type": "TextBlock",
            "size": "Medium",
            "weight": "Bolder",
            "text": f"📢 {title}",
            "wrap": True,
            "color": "Accent",
        },
    ]

    if headline:
        body_elements.append(
            {
                "type": "TextBlock",
                "text": headline,
                "wrap": True,
                "isSubtle": True,
                "spacing": "Small",
            }
        )

    body_elements.append(
        {
            "type": "FactSet",
            "facts": facts,
            "spacing": "Medium",
        }
    )

    actions: list[dict] = [
        {
            "type": "Action.OpenUrl",
            "title": "Open in TenderAI ↗",
            "url": app_url,
        }
    ]

    if valid_source_url:
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "Original Source ↗",
                "url": valid_source_url,
            }
        )

    card_content = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "speak": f"New tender opportunity: {title}",
        "body": body_elements,
        "actions": actions,
    }

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card_content,
            }
        ],
    }


def build_digest_card(tenders: list[dict], portal_base_url: str = DEFAULT_PORTAL_URL) -> dict:
    """Builds a consolidated batch digest Adaptive Card for 4+ qualifying tenders."""
    count = len(tenders)
    body_elements: list[dict] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": f"🎯 TenderAI Pipeline Digest: {count} New Matching Tenders",
            "color": "Accent",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": f"The automated ingestion pipeline finished and found {count} new high-relevance opportunities (Fit 70+).",
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        },
        {"type": "Separator"},
    ]

    # Show top 5 tenders in digest preview
    for idx, t in enumerate(tenders[:5], 1):
        t_title = t.get("title") or "Untitled Tender"
        t_agency = t.get("issuing_agency") or "Unknown Agency"
        t_closing = t.get("closing_date") or "TBD"

        body_elements.append(
            {
                "type": "TextBlock",
                "weight": "Bolder",
                "text": f"{idx}. {t_title}",
                "wrap": True,
                "spacing": "Medium",
            }
        )
        body_elements.append(
            {
                "type": "TextBlock",
                "text": f"🏛️ {t_agency}  •  📅 Closes: {t_closing}",
                "wrap": True,
                "isSubtle": True,
                "spacing": "None",
            }
        )

    if count > 5:
        remaining = count - 5
        body_elements.append(
            {
                "type": "TextBlock",
                "text": f"...and {remaining} more matching tenders in the portal.",
                "isSubtle": True,
                "italic": True,
                "spacing": "Medium",
            }
        )

    actions = [
        {
            "type": "Action.OpenUrl",
            "title": f"Review All {count} Tenders in TenderAI ↗",
            "url": portal_base_url,
        }
    ]

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "speak": f"TenderAI Pipeline Digest: {count} new matching tenders found",
                    "body": body_elements,
                    "actions": actions,
                },
            }
        ],
    }


def build_test_card(portal_base_url: str = DEFAULT_PORTAL_URL) -> dict:
    """Builds an instant verification test card for connection testing."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "speak": "TenderAI Teams connection test successful",
                    "body": [
                        {
                            "type": "TextBlock",
                            "size": "Large",
                            "weight": "Bolder",
                            "text": "🎉 TenderAI Teams Connection Successful!",
                            "color": "Good",
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": (
                                "Your Microsoft Teams webhook is active and verified. "
                                "Newly scraped tenders meeting your relevance threshold (Fit ≥ 70) "
                                "will automatically be posted here."
                            ),
                            "wrap": True,
                            "spacing": "Small",
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "System:", "value": "TenderAI Alerts Engine"},
                                {
                                    "title": "Tested At:",
                                    "value": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                                },
                                {"title": "Status:", "value": "Connected ✅"},
                            ],
                            "spacing": "Medium",
                        },
                    ],
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": "Open TenderAI Portal ↗",
                            "url": portal_base_url,
                        }
                    ],
                },
            }
        ],
    }


build_test_adaptive_card = build_test_card
build_tender_adaptive_card = build_tender_card
build_digest_adaptive_card = build_digest_card


def _do_http_post(url: str, payload: dict) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def send_teams_card(webhook_url: str, card_payload: dict) -> tuple[bool, str]:
    """Sends an Adaptive Card JSON payload to a Microsoft Teams Webhook URL.

    Automatically handles both:
    1. Standard Teams message envelopes: {"type": "message", "attachments": [...]}
    2. Direct AdaptiveCard payloads: {"type": "AdaptiveCard", ...}
    If the endpoint rejects the envelope with HTTP 400, it automatically retries with the raw card.

    Returns:
        (success: bool, message: str)
    """
    clean_url = (webhook_url or "").strip()
    if not clean_url:
        return False, "Missing or empty Teams webhook URL."

    if not (clean_url.startswith("https://") or clean_url.startswith("http://localhost") or clean_url.startswith("http://127.0.0.1")):
        return False, "Webhook URL must be a secure HTTPS address (or localhost for local testing)."

    # Attempt 1: Send provided payload (usually the Teams message envelope)
    try:
        status, _ = _do_http_post(clean_url, card_payload)
        if 200 <= status < 300:
            return True, "Notification delivered to Teams successfully."
        return False, f"Teams webhook responded with unexpected status code: {status}"
    except urllib.error.HTTPError as exc:
        err_msg = exc.read().decode("utf-8", errors="replace")

        # Attempt 2: If rejected with 400 and we sent a message envelope, try sending the inner raw AdaptiveCard
        if exc.code == 400 and card_payload.get("type") == "message":
            try:
                raw_card = card_payload.get("attachments", [{}])[0].get("content")
                if raw_card:
                    status2, _ = _do_http_post(clean_url, raw_card)
                    if 200 <= status2 < 300:
                        return True, "Notification delivered to Teams successfully (raw card format)."
            except Exception:
                pass  # Fall through to report original error

        logger.error("Teams webhook HTTPError %d: %s", exc.code, err_msg)
        return False, f"Teams webhook rejected request (HTTP {exc.code}): {err_msg[:200]}"
    except urllib.error.URLError as exc:
        logger.error("Teams webhook connection failed: %s", exc.reason)
        return False, f"Could not reach Teams webhook endpoint: {exc.reason}"
    except Exception as exc:
        logger.error("Unexpected error sending Teams card: %s", exc)
        return False, f"Internal error dispatching Teams card: {str(exc)}"
