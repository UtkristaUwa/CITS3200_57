#!/usr/bin/env bash
# web_scrapers/deploy.sh
#
# Build, deploy and schedule the scraper stage. Safe to rerun: every step is
# create-or-update. Run from the repo root:
#
#     bash web_scrapers/deploy.sh
#
# Prerequisites: gcloud authenticated with deploy rights on tenderai-dev.

set -euo pipefail

PROJECT_ID="tenderai-dev"
REGION="australia-southeast1"
JOB="tender-scrapers"
SCHEDULE_JOB="tender-scrapers-daily"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/tender-repo/${JOB}"
SERVICE_ACCOUNT="170228060686-compute@developer.gserviceaccount.com"

# 5am daily. Ramon confirmed once a day is enough -- do not make this more
# frequent without asking; these are public portals and we are a guest on them.
CRON="0 5 * * *"
TIMEZONE="Australia/Perth"

# -----------------------------------------------------------------------------
# 1. Build. Cloud Build, not a local `docker build`: the image must be
#    linux/amd64 for Cloud Run, and a build on an Apple Silicon machine produces
#    arm64, which fails to start with no useful error.
# -----------------------------------------------------------------------------
echo "Building ${IMAGE}:latest ..."
gcloud builds submit --config web_scrapers/cloudbuild.yaml --project "$PROJECT_ID"

# -----------------------------------------------------------------------------
# 2. Deploy the Cloud Run job.
#    2 CPU / 4Gi: the VIC and QLD scrapers drive a real Chrome, which needs far
#    more than the AusTender-only path.
#    The "^@^" prefix changes gcloud's delimiter from "," to "@" -- without it
#    SOURCES=austender,vic,qld is silently parsed as three separate variables.
# -----------------------------------------------------------------------------
echo "Deploying Cloud Run job ${JOB} ..."
if gcloud run jobs describe "$JOB" --region "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  ACTION=update
else
  ACTION=create
fi
gcloud run jobs "$ACTION" "$JOB" \
  --image "${IMAGE}:latest" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --tasks 1 \
  --max-retries 1 \
  --task-timeout 3600 \
  --cpu 2 \
  --memory 4Gi \
  --service-account "$SERVICE_ACCOUNT" \
  --set-env-vars "^@^SOURCES=austender,vic,qld@LIMIT=10@MAX_PAGES=2@OUTPUT_PREFIX=raw"

# -----------------------------------------------------------------------------
# 3. Let the scheduler run this job -- scoped to this job, not project-wide.
# -----------------------------------------------------------------------------
gcloud run jobs add-iam-policy-binding "$JOB" \
  --region "$REGION" --project "$PROJECT_ID" \
  --member "serviceAccount:${SERVICE_ACCOUNT}" \
  --role roles/run.invoker >/dev/null

# -----------------------------------------------------------------------------
# 4. Daily schedule.
# -----------------------------------------------------------------------------
echo "Scheduling ${SCHEDULE_JOB} (${CRON} ${TIMEZONE}) ..."
gcloud services enable cloudscheduler.googleapis.com --project "$PROJECT_ID"

if gcloud scheduler jobs describe "$SCHEDULE_JOB" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  SCHED_ACTION=update
else
  SCHED_ACTION=create
fi
gcloud scheduler jobs "$SCHED_ACTION" http "$SCHEDULE_JOB" \
  --location "$REGION" \
  --project "$PROJECT_ID" \
  --schedule "$CRON" \
  --time-zone "$TIMEZONE" \
  --uri "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run" \
  --http-method POST \
  --oauth-service-account-email "$SERVICE_ACCOUNT" \
  --attempt-deadline 180s \
  --description "Daily tender scrape (austender, vic, qld) at 5am ${TIMEZONE}."

cat <<NOTE

Done.

  Run now      : gcloud run jobs execute ${JOB} --region ${REGION} --project ${PROJECT_ID}
  Test schedule: gcloud scheduler jobs run ${SCHEDULE_JOB} --location ${REGION} --project ${PROJECT_ID}
  Logs         : gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="${JOB}"' --project ${PROJECT_ID} --limit 50

Not set here, on purpose:

  AUSTENDER_USERNAME / AUSTENDER_PASSWORD
      Attachment downloads need a registered-user session. Put the credentials
      in Secret Manager and attach them with --set-secrets; do not pass them as
      plain env vars.

  OUTPUT_BUCKET
      Off until we settle whether the scraper stage uploads or hands folders to
      the manager function. With it unset the scrape stays in the container's
      temp directory, which is the current contract.
NOTE
