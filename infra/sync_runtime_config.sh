#!/usr/bin/env bash
# infra/sync_runtime_config.sh
#
# Moves processing/tender_processor.cfg between this repo and the shared copy
# in Google Cloud Storage that the admin page and the batch job actually use.
#
# There are two writers to one file, so the direction matters:
#
#   GCS is authoritative for prompt CONTENT. The admin config page edits it
#   live, and the batch job reads it at startup. Those edits exist nowhere else.
#
#   Git is authoritative for STRUCTURE — new keys, new sections, anything a code
#   change depends on. tender_processor.py only applies a field description when
#   `field_<name>` exists in the cfg, so shipping a new extraction field without
#   pushing its cfg half leaves the model with an undocumented field and no
#   error anywhere. Treat a structural cfg change like a DB migration: the merge
#   is not finished until it has been pushed.
#
# Usage:
#   bash infra/sync_runtime_config.sh diff    # what differs, changes nothing
#   bash infra/sync_runtime_config.sh pull    # GCS  -> repo, after admin edits
#   bash infra/sync_runtime_config.sh push    # repo -> GCS, after a code change
#
# Prerequisites: gcloud CLI, authenticated, with read (and write, for push)
# on the runtime config bucket.

set -euo pipefail

BUCKET="${RUNTIME_CONFIG_BUCKET:-tenderai-dev-runtime-config}"
OBJECT="${RUNTIME_CONFIG_OBJECT:-runtime/tender_processor.cfg}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
LOCAL_CFG="$REPO_ROOT/processing/tender_processor.cfg"
REMOTE_URI="gs://${BUCKET}/${OBJECT}"

MODE="${1:-}"
if [[ "$MODE" != "diff" && "$MODE" != "pull" && "$MODE" != "push" ]]; then
  echo "Usage: bash infra/sync_runtime_config.sh [diff|pull|push]" >&2
  exit 2
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
REMOTE_COPY="$TMP_DIR/remote.cfg"

# A pre-flight only. The batch job re-validates on download (and silently falls
# back to the image's copy if it fails, which is exactly the failure this is
# here to prevent); the API re-validates anything it writes itself. The
# authoritative list lives in processing/runtime_config.py::_validate_config.
validate() {
  python3 - "$1" <<'PY'
import configparser
import sys

path = sys.argv[1]
required_sections = {
    "models", "relevance_prompts", "relevance_scoring", "taxonomies",
    "system_prompts", "field_descriptions", "prompt_templates",
}
required_options = {
    "models": ("triage_model", "extraction_model", "relevance_model"),
    "relevance_prompts": (
        "classification_thoughts", "focus_areas", "work_types", "out_of_scope",
    ),
}

parser = configparser.ConfigParser(interpolation=None, strict=True)
try:
    with open(path, encoding="utf-8") as handle:
        parser.read_file(handle)
except (configparser.Error, UnicodeDecodeError) as exc:
    sys.exit(f"invalid CFG: {exc}")

missing = sorted(required_sections.difference(parser.sections()))
if missing:
    sys.exit(f"missing section(s): {', '.join(missing)}")

for section, keys in required_options.items():
    for key in keys:
        if not parser.has_option(section, key):
            sys.exit(f"missing {section}.{key}")
        if not parser.get(section, key).strip():
            sys.exit(f"empty {section}.{key}")
PY
}

echo "Local : $LOCAL_CFG"
echo "Remote: $REMOTE_URI"
echo

if ! gcloud storage cp "$REMOTE_URI" "$REMOTE_COPY" >/dev/null 2>&1; then
  if [[ "$MODE" == "push" ]]; then
    echo "No object at $REMOTE_URI yet — this push will seed it."
    : > "$REMOTE_COPY"
  else
    echo "Could not read $REMOTE_URI (missing, or no access)." >&2
    exit 1
  fi
fi

if diff -q "$REMOTE_COPY" "$LOCAL_CFG" >/dev/null 2>&1; then
  echo "In sync — repo and GCS are byte-identical."
  [[ "$MODE" == "diff" ]] && exit 0
  echo "Nothing to $MODE."
  exit 0
fi

echo "They differ (- remote, + local):"
echo
diff -u --label "remote ($REMOTE_URI)" --label "local (repo)" \
  "$REMOTE_COPY" "$LOCAL_CFG" || true
echo

case "$MODE" in
  diff)
    echo "Run with 'pull' to take the remote version, 'push' to publish the local one."
    ;;

  pull)
    validate "$REMOTE_COPY"
    cp "$REMOTE_COPY" "$LOCAL_CFG"
    echo "Pulled into the repo. Review and commit:"
    echo "  git diff processing/tender_processor.cfg"
    ;;

  push)
    validate "$LOCAL_CFG"
    # Everything in the remote that is not in the local copy is about to be
    # destroyed, and prompt tuning done through the admin page lives only there.
    echo "This OVERWRITES the live config. Any admin-page edit shown as a '-'"
    echo "line above will be lost. Consider 'pull' first and merging by hand."
    echo
    read -r -p "Type 'push' to continue: " confirm
    if [[ "$confirm" != "push" ]]; then
      echo "Aborted."
      exit 1
    fi
    gcloud storage cp "$LOCAL_CFG" "$REMOTE_URI"
    echo
    echo "Pushed. The API serves it immediately; the batch job picks it up on"
    echo "its next run (config is read once at job startup)."
    ;;
esac
