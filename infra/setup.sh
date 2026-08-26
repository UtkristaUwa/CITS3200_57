# Prerequisites:
#   - gcloud CLI installed and authenticated (`gcloud auth login`)
#   - firebase-tools installed (`npm install -g firebase-tools`)
#   - Owner or Editor role on the GCP project 

set -euo pipefail

PROJECT_ID="tenderai-dev"
PROJECT_NAME="TenderAI Dev"

# ---------------------------------------------------------------------------
# 1. Create the GCP project
# ---------------------------------------------------------------------------
echo "Creating project: $PROJECT_ID"
gcloud projects create "$PROJECT_ID" --name="$PROJECT_NAME" || \
  echo "Project may already exist, continuing..."

gcloud config set project "$PROJECT_ID"

# ---------------------------------------------------------------------------
# 2. Enable required APIs
# ---------------------------------------------------------------------------
# Billing is linked to this project — all APIs below (including the
# billing-gated ones) can be enabled in one pass.

echo "Enabling core APIs..."
gcloud services enable \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com \
  firebase.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  bigquery.googleapis.com \
  cloudfunctions.googleapis.com

# ---------------------------------------------------------------------------
# 3. Link Firebase to this project (Hosting only needs the free Spark plan)
# ---------------------------------------------------------------------------
# Manual step (Firebase console does not have a full CLI equivalent for
# initial project linking):
#   1. Go to https://console.firebase.google.com
#   2. "Add Firebase to Google Cloud project" -> select tenderai-dev
#
# Once linked, initialize Hosting locally from frontend/:
#   cd frontend
#   firebase login
#   firebase init hosting
#     - Use existing project: tenderai-dev
#     - Public directory: dist
#     - Configure as single-page app: Yes
#   npm run build
#   firebase deploy --only hosting

# ---------------------------------------------------------------------------
# 4. GitHub Actions CI/CD
# ---------------------------------------------------------------------------
# Generated via: firebase init hosting:github (run from frontend/)
# This creates:
#   .github/workflows/firebase-hosting-merge.yml   (deploy on push to main)
#   .github/workflows/firebase-hosting-pull-request.yml (PR preview links)
# and adds FIREBASE_SERVICE_ACCOUNT_TENDERAI_DEV as a GitHub repo secret
# automatically. Verify it exists under:
#   Repo Settings -> Secrets and variables -> Actions


echo "Done. See comments above for manual/billing-gated steps still pending."
