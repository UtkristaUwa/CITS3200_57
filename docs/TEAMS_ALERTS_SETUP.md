# Microsoft Teams Tender Alerts — Setup & Handover Guide

TenderAI provides automated Microsoft Teams notifications for newly ingested tender opportunities that score high relevance against organizational priorities (Fit Score ≥ 70).

```
 Government Portals (Vic, NSW, ACT, Austender, etc.)
                   │
                   ▼
       TenderAI Scraper Pipeline (manager.py)
                   │
                   ▼
         AI Relevance Scoring (Fit 0–100)
                   │
                   ▼
     Newly Inserted & Fit ≥ 70?
       ├── No  ──▶ (Silent — no spam)
       └── Yes ──▶ HTTP POST (Adaptive Card)
                   │
                   ▼
       Microsoft Teams Channel (#procurement-tenders)
```

---

## Access & Team Visibility Model

* **One Webhook for the Entire Team**: You do **not** configure webhooks per user or per employee. A single incoming webhook connects TenderAI to a designated Teams channel (e.g. `#procurement-tenders` or `#tender-leads`).
* **Instant Collaboration**: Every team member who is in that Teams channel will automatically see the tender alert cards, complete with the AI headline, key metadata, and direct buttons to open the tender in TenderAI or on the original government portal.
* **No Azure App Registrations or Admin Consent Required**: Teams channel owners can set up the webhook workflow in under 2 minutes without tenant administrator approval.

---

## Part A — Create the Webhook in Microsoft Teams (2 Minutes)

Microsoft Teams uses **Workflows** (powered by Power Automate) for channel webhooks:

1. Open **Microsoft Teams** (desktop or browser).
2. Go to your team and navigate to the channel where you want notifications (e.g. `General` or create `#procurement-tenders`).
3. Click the three dots (**`...`**) next to the channel name &rarr; select **Workflows**.
4. In the Workflows gallery, search for:
   > **"Post to a channel when a webhook request is received"**
5. Select the template:
   * **Workflow Name**: Enter `TenderAI Alerts` (this name appears as the subtitle in channel posts).
   * **Team & Channel**: Confirm the target team and channel.
6. Click **Create flow** (or **Next**).
7. Teams will generate a unique HTTPS Webhook URL (format: `https://prod-xx.australiaeast.logic.azure.com:443/workflows/...`).
8. Click **Copy** to copy the URL to your clipboard.

---

## Part B — Configure TenderAI

1. Log into the TenderAI web portal as an Administrator.
2. In the top navigation bar, click **Admin** &rarr; select **Tender alerts (Teams)** from the sidebar navigation (`/admin/alerts`).
3. Switch **Enable automated Teams alerts** to **ON**.
4. Paste your copied Webhook URL into the **Teams Webhook URL** field.
5. *(Optional)* Adjust the **Minimum AI Fit Threshold** (defaults to `70`). Only tenders scoring equal to or greater than this score will trigger alerts.
6. Click **Test Webhook Connection**:
   * A test card will immediately appear in your Microsoft Teams channel:
     > *"🎉 TenderAI Teams Connection Successful! Your Microsoft Teams webhook is active and verified."*
7. Click **Save Configuration**.

---

## Notification Delivery Logic

To prevent notification fatigue and channel spam, TenderAI uses an automated hybrid strategy:

| Pipeline Ingestion Outcome | Teams Delivery Action |
|---|---|
| **0 matching tenders** (Fit < 70) | **Silent** (no message sent to prevent channel noise) |
| **1 to 3 matching tenders** (Fit ≥ 70) | **Individual Cards** (detailed card for each opportunity with agency, value, closing date, headline, and direct links) |
| **4+ matching tenders** (Fit ≥ 70) | **Consolidated Batch Digest Card** (clean executive summary of all new opportunities with a one-click button to view all in TenderAI) |

---

## Standalone CLI Testing (for Developers & Operations)

If you wish to test or verify alerts from the terminal without opening the browser:

```bash
# 1. Test using an explicit webhook URL:
python test_teams_alert.py https://prod-xx.australiaeast.logic.azure.com/...

# 2. Test using the configured URL stored in Firestore:
python test_teams_alert.py

# 3. Test sending a realistic single high-relevance tender card:
python test_teams_alert.py --mode tender

# 4. Test sending a batch digest card:
python test_teams_alert.py --mode digest
```
