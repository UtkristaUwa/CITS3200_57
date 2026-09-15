#!/usr/bin/env bash
# infra/apply_schema.sh
#
# Applies Schema/schema.sql and any pending Schema/migrations/*.sql to the
# live BigQuery dataset. Safe to rerun: schema.sql only uses
# CREATE TABLE IF NOT EXISTS, and migrations are written as idempotent
# CREATE OR REPLACE statements.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated, with the `bq` component
#   - Project set: gcloud config set project tenderai-dev
#
# Usage: bash infra/apply_schema.sh

set -euo pipefail

PROJECT_ID="tenderai-dev"
DATASET="TenderAI"
# Only used if the dataset doesn't exist yet (it already does in tenderai-dev
# today, created out-of-band) — confirm/adjust this if you ever run this
# against a fresh project.
LOCATION="australia-southeast1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Ensuring dataset ${PROJECT_ID}:${DATASET} exists..."
bq show --dataset "${PROJECT_ID}:${DATASET}" >/dev/null 2>&1 || \
  bq mk --dataset --location="$LOCATION" "${PROJECT_ID}:${DATASET}"

echo "Applying base schema (Schema/schema.sql)..."
bq query --use_legacy_sql=false < "$REPO_ROOT/Schema/schema.sql"

echo "Applying pending migrations (Schema/migrations/*.sql)..."
shopt -s nullglob
for migration in "$REPO_ROOT"/Schema/migrations/*.sql; do
  echo "  -> $(basename "$migration")"
  bq query --use_legacy_sql=false < "$migration"
done

echo "Done."
