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
Copy `frontend/.env.example` to `frontend/.env.local` (not committed):
```
VITE_API_BASE_URL=http://localhost:8000
VITE_ENTRA_TENANT_ID=<Directory (tenant) ID — see docs/SSO_SETUP.md>
```

### Sign-in locally
Two sign-in methods, both ending at a Firebase ID token that the API verifies:

- **Microsoft (Entra ID SSO)** — the button on the login page. Needs the app
  registration and the Firebase provider config from
  [docs/SSO_SETUP.md](docs/SSO_SETUP.md). `localhost` is already an authorized
  domain in Firebase Auth.
- **Email + password** — invite-only. An admin invites you from `/admin/users`,
  which emails a link to set your own password.

`auth/operation-not-allowed` on the Microsoft button means the provider is not
enabled in the Firebase console yet.

There is no Google OAuth and no `VITE_GOOGLE_CLIENT_ID` any more — Firebase Auth
replaced it.

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

**Every endpoint except `/health` now requires a Firebase ID token.** Calling the
API with curl or from `/docs` needs a real token in an `Authorization: Bearer`
header — grab one from the browser devtools console with
`await firebase.auth().currentUser.getIdToken()` while signed in to the SPA.
A first Microsoft sign-in auto-creates the caller's `users/{uid}` profile; see
`api/app/auth.py`.

## Admin users
`users/{uid}.isAdmin` gates the `/admin` pages, the Users page and the
`inviteUser` function. It is set at invite time, or afterwards by an existing
admin toggling the switch on `/admin/users` (which calls the `setUserAdmin`
function — the browser cannot write the field directly).

An admin cannot demote themselves. That is what stops the last admin from
locking everyone out, so don't remove the check.

**Bootstrapping an SSO admin:** signing in with Microsoft creates a *new* Firebase
uid with `isAdmin: false`, unrelated to any existing password account. Sign in
with Microsoft once so the profile exists, then promote it from an account that
is already an admin. If no admin exists at all, set the field by hand in the
Firestore console once.

Tests: `cd tests/functions && npm install && npm test`.

## Firestore rules
`firestore.rules` locks down what the browser may write to `users/{uid}` — the
document that holds both a user's favourites and their `isAdmin` flag. Deploy it
with `firebase deploy --only firestore:rules`; test it with
`cd tests/firestore_rules && npm install && npm test` (emulator only, no cloud
project touched).

## Deploys
- **Frontend:** auto-deploys to Firebase Hosting on every merge to `main` via GitHub Actions (`.github/workflows/firebase-hosting-merge.yml`). PRs get a preview URL automatically.
- **API:** not yet wired up — pending Cloud Run setup (blocked on billing, see Part 1 notes).

## Who to ask
- Cloud resources, deploy pipeline, auth/Client ID: Part 1
- Frontend/UI: Part 2
- API/database: Part 3
- Data ingestion/scraping: Part 4
- AI summarisation/admin console: Part 5

## Known gaps (update as resolved)
- `inviteUser` generates a password-setup link pointing at `http://localhost:5173`
  (`functions/src/index.ts`) — fine for testing, broken for a real invite.
- The `get-tenders` Cloud Run service has no token check and was deployed with
  invoker IAM disabled. Only `tenderai-api` is covered by `api/app/auth.py`.
- The Entra client secret expires — diarise the date from docs/SSO_SETUP.md.
- `ai/` folder not yet scaffolded

