# TenderAI: Automated Tender Discovery and Opportunity Matching

## Group number 57

## Team members

| NAME                     | StudentID   |   GitHubID     |
|--------------------------|-------------|----------------|
| Utkrista Sen             | 24145884    | UtkristaUwa    |
| Sepehr Moghani Pilehroud | 23642415    | sepehrmoghani  |
| Jinghao Hu | 24183532 | jinghao163 |
| Lucan McDonald | 24214099 | PiesOnTues |
| Benjamin Gilmore | 23706738 | bgilmore22 |
| Radrados                 | Radrados         | 23423175    |


# TenderAI

An automated opportunity-discovery and matching system built for Social Ventures Australia (SVA).

## Overview

SVA works with government, philanthropy, businesses and for-purpose organisations across 
consulting, social impact investing and sector development. Relevant tenders, RFQs, grants 
and partnership opportunities are scattered across many government and institutional sources, 
and are easily missed since staff can't monitor every source continuously.

TenderAI will:
- Monitor a configurable set of procurement and funding sources
- Identify new opportunities and extract key information
- Remove duplicates
- Assess relevance against SVA's organisational interest profiles (e.g. evaluation, strategy, 
  employment, early childhood, housing, First Nations economic participation, social impact 
  investing, responsible AI)
- Write relevant opportunities to a central database/spreadsheet
- Notify staff via Microsoft Teams (or similar)
- Let users see why an opportunity was matched, review source docs, and accept/reject with feedback
- Provide an admin interface to manage sources, profiles, keywords, thresholds and system status



# How TenderAI Works

TenderAI is built from three flows that share one BigQuery database and one Cloud Storage bucket:

1. **The daily pipeline** finds new tenders, reads them with AI and saves them.
2. **The user flow** lets SVA staff sign in, search, filter, favourite and download tenders.
3. **The admin flow** lets admins manage users, check scraper health and tune the AI.

> The diagrams use Mermaid, which GitHub renders automatically.

```CITS3200_57/
├── .github/
│   └── workflows/
│       ├── deploy-backend.yml
│       └── deploy-frontend.yml
├── api/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── config.py
│   │   ├── main.py
│   │   ├── query_parser.py
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── admin.py
│   │   │   ├── locations.py
│   │   │   └── tenders.py
│   │   ├── schemas.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── bigquery_service.py
│   │   │   ├── classification_service.py
│   │   │   ├── embedding_service.py
│   │   │   ├── firestore_service.py
│   │   │   ├── gemini_service.py
│   │   │   └── scraper_service.py
│   │   └── utils.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── tests/
│       ├── __init__.py
│       ├── test_admin.py
│       ├── test_auth.py
│       ├── test_locations.py
│       ├── test_main.py
│       ├── test_query_parser.py
│       ├── test_schemas.py
│       └── test_tenders.py
├── frontend/
│   ├── .env
│   ├── .env.production
│   ├── .gitignore
│   ├── README.md
│   ├── eslint.config.js
│   ├── firebase.json
│   ├── index.html
│   ├── package-lock.json
│   ├── package.json
│   ├── public/
│   │   └── sva-logo.png
│   ├── src/
│   │   ├── App.tsx
│   │   ├── assets/
│   │   │   └── sva-logo.png
│   │   ├── components/
│   │   │   ├── AdminConfigPage.tsx
│   │   │   ├── AdminRoute.tsx
│   │   │   ├── ForgotPasswordPage.tsx
│   │   │   ├── LoginPage.tsx
│   │   │   ├── ManageUsersPage.tsx
│   │   │   ├── NavBar.tsx
│   │   │   ├── RegisterPage.tsx
│   │   │   ├── TenderDetailPage.tsx
│   │   │   ├── TenderFilterBar.tsx
│   │   │   ├── TenderListPage.tsx
│   │   │   └── TenderTable.tsx
│   │   ├── contexts/
│   │   │   └── AuthContext.tsx
│   │   ├── firebaseConfig.ts
│   │   ├── hooks/
│   │   │   └── useTenderFilters.ts
│   │   ├── main.tsx
│   │   ├── services/
│   │   │   └── api.ts
│   │   ├── theme.ts
│   │   └── vite-env.d.ts
│   ├── tsconfig.app.json
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   └── vite.config.ts
├── scrapers/
│   ├── Dockerfile
│   ├── main.py
│   ├── requirements.txt
│   ├── run_scraper.sh
│   └── spiders/
│       ├── __init__.py
│       ├── austender_spider.py
│       ├── base_spider.py
│       ├── buysa_spider.py
│       ├── eprocure_spider.py
│       ├── gem_spider.py
│       ├── nsw_spider.py
│       ├── qld_spider.py
│       ├── tenders_vic_spider.py
│       └── wa_spider.py
├── .gitignore
├── LICENSE
└── README.md
```

## The big picture

```mermaid
flowchart LR
    subgraph Daily["Daily pipeline (5 am AWST)"]
        SCHED[Cloud Scheduler] --> JOB["Cloud Run job<br/>tender-batch-job<br/><i>manager.py</i>"]
        JOB --> SCR["error_scrapers/<br/>7 portals"]
        JOB --> AS["attachment_store.py"]
        JOB --> AI["processing/<br/>Gemini"]
        JOB --> ING["ingestion/<br/>bigquery_client.py"]
    end

    subgraph Storage["Google Cloud storage"]
        GCS[("Cloud Storage<br/>attachments · scraper_health.json<br/>tender_processor.cfg")]
        BQ[("BigQuery<br/>TenderAI dataset<br/><i>Schema/</i>")]
    end

    subgraph App["Web app"]
        FE["frontend/<br/>React · Firebase Hosting"]
        API["api/<br/>FastAPI · Cloud Run"]
        FA["Firebase Auth<br/>+ functions/"]
        FS[("Firestore<br/>users · favourites · isAdmin")]
    end

    AS --> GCS
    ING --> BQ
    JOB -- health status --> GCS
    GCS -- live prompts --> AI

    FE -- sign in --> FA
    FE -- favourites --> FS
    FE -- "requests + Firebase token" --> API
    API --> BQ
    API --> GCS
    FA --> FS
```

---

## 1. Daily pipeline: `manager.py`

Cloud Scheduler starts the Cloud Run job `tender-batch-job` at 5 am Perth time. The job runs the `Dockerfile` image, and that image runs `manager.py`. Cloud Build (`cloudbuild.yaml`) rebuilds the image when code changes.

```mermaid
flowchart TD
    START([5 am: job starts]) --> CFG["Load AI config<br/><b>processing/runtime_config.py</b><br/>download tender_processor.cfg from GCS<br/>(falls back to the copy in the image)"]
    CFG --> LOOP{{"For each portal, one at a time:<br/>AusTender · GrantConnect · buy.nsw · Tenders ACT<br/>NT QTOL · QLD QTenders · Buying for Victoria"}}

    LOOP --> S1["<b>1. SCRAPE</b><br/>error_scrapers/&lt;portal&gt;/scraper.py<br/>skips tenders whose URL is already in BigQuery"]
    S1 --> OUT["Temp folder per tender:<br/>__tender__REF.txt (page text)<br/>original attachments<br/>attachment .txt (extracted text)"]
    S1 --> HEALTH["Status code → scraper_health.json in GCS<br/>(shown on the System Health page)"]

    OUT --> T{{"For each tender folder"}}
    T --> S2["<b>2. STORE</b><br/>attachment_store.py<br/>upload originals to Cloud Storage<br/>delete the local copies"]
    S2 --> S3["<b>3. PROCESS</b><br/>processing/tender_processor.py (Gemini)<br/>triage docs → extract fields → headline + summary"]
    S3 --> S4["<b>4. SCORE</b><br/>processing/relevance_determination.py<br/>focus areas + work types → fit score 0–100 + reason"]
    S4 --> S5["<b>5. EMBED</b><br/>title + summary → 768-dim vector<br/>(used for semantic search)"]
    S5 --> S6["<b>6. SAVE</b><br/>ingestion/bigquery_client.py upsert_tender()<br/>new → insert · unchanged → noop · changed → update + snapshot"]

    S6 --> CLEAN["Delete this portal's temp folders<br/>(frees memory before the next portal)"]
    CLEAN --> LOOP
    LOOP -- all portals done --> END([Log run summary · exit])
```

### Step by step

| # | Stage | Code | Reads | Writes |
|---|---|---|---|---|
| 0 | Load config | `processing/runtime_config.py` | `tender_processor.cfg` in GCS | Local copy for this run |
| 1 | Scrape | `error_scrapers/<portal>/scraper.py` + `common.py` | Portal websites, known URLs from BigQuery | Temp folder per tender, status code |
| 1b | Health | `manager.py` → `publish_health_status_to_gcs` | Status codes | `scraper_health.json` in GCS |
| 2 | Store attachments | `attachment_store.py` | Temp attachments | GCS bucket, plus document records |
| 3 | AI processing | `processing/tender_processor.py` | `.txt` files in the tender folder | Structured tender record |
| 4 | Relevance | `processing/relevance_determination.py` | Tender record | `focus_areas`, `work_types`, `fit`, `fit_reason` |
| 5 | Embedding | `manager.py` → `generate_embedding` | Title + summary | `embedding` vector |
| 6 | Save | `ingestion/bigquery_client.py` | Final record | `tenders` + `tender_snapshots` tables |

**Points worth knowing**
- Portals run **one at a time**, and each portal's files are deleted before the next starts, so memory use stays low. Tenders from portals that already finished are saved even if a later portal crashes.
- If a scraper fails, the run still continues. The failure shows up on the System Health page, and the job exits with an error code.
- If relevance scoring fails, the tender is **still saved**, with empty fit fields. If AI processing fails, that tender is skipped.
- Running `python manager.py --local` only scrapes 2 tenders per site into a local folder. It does no AI processing and touches no cloud services.

---

## 2. User flow: finding a tender

```mermaid
sequenceDiagram
    actor U as SVA staff
    participant FE as frontend (React)
    participant FA as Firebase Auth
    participant FS as Firestore
    participant API as api (FastAPI)
    participant BQ as BigQuery
    participant GCS as Cloud Storage

    U->>FE: Open site
    FE->>FA: Sign in (Microsoft SSO or invited email/password)
    FA-->>FE: Firebase ID token
    FE->>API: GET /me (token)
    API->>FS: Create users/{uid} on first sign-in
    FE->>API: GET /tenders?search=…&filters (token)
    API->>BQ: Vector search (if search text) or newest first
    BQ-->>API: Tenders + fit score + reason
    API-->>FE: Tender list
    FE-->>U: TenderCards (summary, fit, why matched)
    U->>FE: ★ Favourite
    FE->>FS: Add to users/{uid}.favorites
    U->>FE: Download documents
    FE->>API: GET /documents/download or /tenders/{id}/documents/zip
    API->>GCS: Stream file(s)
    GCS-->>U: File / zip
```

| What the user does | Frontend | API / backend |
|---|---|---|
| Sign in | `LoginPage`, `lib/AuthContext.tsx`, `lib/firebase.ts` | Firebase Auth; `api/app/auth.py` checks the token |
| Browse / search / filter | `TendersPage`, `TenderFilterBar`, `lib/useTenderFilters.ts` | `GET /tenders`, `GET /locations` → `api/app/bigquery.py` |
| See why it matched | `TenderCard` | `fit`, `fit_reason`, `focus_areas`, `work_types` columns |
| Favourite | `FavoritesPage`, `lib/FavoritesContext.tsx` | Firestore `users/{uid}` (protected by `firestore.rules`) |
| Share | `ShareTenderDialog`, `lib/tenderShare.ts` | none (link only) |
| Download docs | `TenderDocuments` | `GET /documents/download`, `GET /tenders/{id}/documents/zip` (`document_zip.py`) |

---

## 3. Admin flow

Pages under `/admin` (`AdminLayout`) are only shown to users whose `users/{uid}.isAdmin` is `true`.

```mermaid
flowchart LR
    ADMIN([Admin]) --> UM["User Management<br/>/admin/users"]
    ADMIN --> SH["System Health<br/>/admin/health"]
    ADMIN --> CP["Config<br/>/admin/config"]

    UM -- inviteUser --> FN["functions/src/index.ts<br/>(Cloud Functions)"]
    UM -- setUserAdmin --> FN
    FN --> FA[Firebase Auth] & FS[(Firestore users)]
    FN -- set-password email --> NEW([New user])

    SH -- "GET /admin/health/scrapers" --> API1["api/routers/health.py"]
    API1 --> HJ[("scraper_health.json<br/>written by manager.py")]

    CP -- "GET/PATCH /models · /prompts · /relevance" --> API2["api/routers/admin_config.py<br/>api/app/runtime_config.py"]
    API2 --> CFG[("tender_processor.cfg in GCS")]
    CFG -. read at next 5 am run .-> PIPE["Daily pipeline"]
```

**How the AI config loop works.** An admin edits prompts, models or scoring weights on the Config page. The API saves those changes to `tender_processor.cfg` in Cloud Storage. The next daily run downloads that file, so the changes apply from the next morning. `infra/sync_runtime_config.sh pull|push|diff` keeps the git copy (`processing/tender_processor.cfg`) in step with the live one. If the pipeline can't read the live config, it uses the copy in its image, and `infra/alert_runtime_config_fallback.yaml` raises an alert.

---

## 4. Deploying and supporting code

```mermaid
flowchart LR
    DEV([Push / merge to main]) --> GHA[".github/workflows/<br/>firebase-hosting-merge.yml"] --> HOST[Frontend → Firebase Hosting]
    PR([Open a PR]) --> GHP["firebase-hosting-pull-request.yml"] --> PREV[Preview URL]
    CB["gcloud builds submit<br/>cloudbuild.yaml"] --> IMG[Pipeline image → Artifact Registry] --> RJ[Update tender-batch-job]
    APID["api/Dockerfile"] --> APIRUN[API → Cloud Run]
    FBD["firebase deploy"] --> FUNCS["functions/ + firestore.rules"]
    SQL["infra/apply_schema.sh"] --> BQS["Schema/schema.sql + migrations → BigQuery"]
```

| Folder | Role in the flow |
|---|---|
| `infra/` | One-off and maintenance scripts: GCP setup, schema apply, config sync, alerting |
| `Schema/` | Defines the BigQuery tables everything reads from and writes to |
| `tests/` | `pytest` for the pipeline and API (offline fixtures; `--live` hits real portals); Node tests for Firestore rules and Functions |
| `documentation/` | Owner manual, SSO setup, full docs |
| `web_scrapers/` | Old scrapers, **not in the flow**, kept for reference |

---

## Data at a glance

| Where | What lives there | Written by | Read by |
|---|---|---|---|
| BigQuery `tenders` | One row per tender (fields, summary, fit, embedding, document links) | `ingestion/bigquery_client.py` | `api/`, `manager.py` (dedupe) |
| BigQuery `tender_snapshots` | History of each change to a tender | `ingestion/bigquery_client.py` | Audit / history |
| Cloud Storage `tenderai-dev-documents` | Original attachments, `scraper_health.json`, crawl cursors | `attachment_store.py`, `manager.py`, `error_scrapers/crawl_state.py` | `api/` (downloads, health) |
| Cloud Storage `tenderai-dev-runtime-config` | Live `tender_processor.cfg` | Admin Config page via `api/` | `processing/runtime_config.py` |
| Firestore `users/{uid}` | Profile, favourites, `isAdmin` | `api/app/auth.py`, frontend, `functions/` | Frontend, `functions/` |
| Secret Manager | Tender portal logins | Set by hand | `tender-batch-job` |
