# TenderAI — Local Development Setup

## Stack
Google Cloud . Frontend: React + Vite + TypeScript. Backend/ingestion: Python (FastAPI, Cloud Functions).

## Prerequisites
- Node.js 20+
- Python 3.11+
- Git
- [gcloud CLI](https://cloud.google.com/sdk/docs/install) (only needed if you're touching infra)

## Clone the repo
```bash
git clone https://github.com/UtkristaUwa/CITS3200_57
cd CITS3200_57
```

## Frontend (`frontend/`)
```bash
cd frontend
npm install
npm run dev
```
Runs at `http://localhost:5173`.

### Environment variables
Create `frontend/.env.local` (not committed) with:
```
VITE_GOOGLE_CLIENT_ID=<ask Part 1 for the current Client ID>
VITE_API_BASE_URL=http://localhost:8000
```

### Sign-in locally
Login uses Google OAuth. `localhost:5173` is already registered as an authorized origin — no extra setup needed beyond the Client ID above. Ask Part 1 to add your Google account as a test user if login fails with an "access blocked" error (the app is in Testing mode).

## API (`api/`)
```bash
cd api
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
gcloud auth application-default login   # if you haven't already
uvicorn app.main:app --reload
```
Runs at `http://localhost:8000`, interactive docs at `http://localhost:8000/docs`.

Copy `api/.env.example` to `api/.env` to configure; set `USE_MOCK_DATA=true` there
if you want fixture tenders without GCP credentials.

## Scrapers (`web_scrapers/`)
```bash
pip install -r web_scrapers/requirements.txt
python -m web_scrapers.run_scrapers --sources austender --limit 5
pytest                    # offline tests
pytest --live             # also hits the real portals
```
Output is one directory per tender under `tenders_data/`. See
[web_scrapers/README.md](web_scrapers/README.md) for the format, the
per-portal document situation and the Cloud Run job.

## Deploys
- **Frontend:** auto-deploys to Firebase Hosting on every merge to `main` via GitHub Actions (`.github/workflows/firebase-hosting-merge.yml`). PRs get a preview URL automatically.
- **API:** not yet wired up — pending Cloud Run setup (blocked on billing, see Part 1 notes).
- **Scrapers:** Cloud Run job `tender-scrapers` in `australia-southeast1`, run on demand.

## Who to ask
- Cloud resources, deploy pipeline, auth/Client ID: Part 1
- Frontend/UI: Part 2
- API/database: Part 3
- Data ingestion/scraping: Part 4
- AI summarisation/admin console: Part 5

## Known gaps (update as resolved)
- OAuth Client Secret not yet stored in Secret Manager (blocked on GCP billing)
- `ai/` folder not yet scaffolded
- API not yet deployed to Cloud Run (works locally; deploy automation pending)

