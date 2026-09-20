#!/usr/bin/env python3
"""
run_pipeline.py — scrape tenders, then validate & submit every one to BigQuery.

This wraps the two manual steps you'd otherwise run by hand:

    python -m web_scrapers.run_scrapers --sources ... --limit ... --output-dir ./out
    (cd ingestion && for each out/*/tender.json: python validate_and_submit.py <file>)

into one command. It deliberately does NOT touch the AI enrichment stage
(processing/tender_processor.py, manager.py) — that's still being worked on
separately. This script only does: scrape -> validate -> submit to BigQuery,
plus an optional attachment publish to Cloud Storage.

Usage:
    python run_pipeline.py --sources austender --limit 5
    python run_pipeline.py --sources austender,vic,qld,wa,nt --limit 0
    python run_pipeline.py --sources austender --limit 10 --output-dir ./out --keep
    python run_pipeline.py --sources austender --limit 10 --publish   # also mirror
                                                                       # attachments to
                                                                       # OUTPUT_BUCKET

Setup (one-time, both requirement files into ONE environment so this script
can import both sides directly):
    python -m venv venv
    venv\\Scripts\\activate        (or: source venv/bin/activate)
    pip install -r ingestion/requirements.txt
    pip install -r web_scrapers/requirements.txt
    gcloud auth application-default login   # if not already done
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
INGESTION_DIR = REPO_ROOT / "ingestion"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(INGESTION_DIR))


def parse_args(argv=None):
    import os

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Every default also reads an environment variable first, matching
    # web_scrapers/run_scrapers.py's own CLI convention. This is what lets a
    # scheduled container (Cloud Run Job + Cloud Scheduler) configure a run
    # entirely through env vars on the job, with no image rebuild and no args
    # baked into the entrypoint.
    parser.add_argument(
        "--sources",
        default=os.environ.get("SOURCES", "austender"),
        help="comma-separated: austender,wa,nt,vic,qld (default: $SOURCES or "
             "'austender')",
    )
    parser.add_argument(
        "--limit", type=int, default=int(os.environ.get("LIMIT", "10")),
        help="max tenders per source, 0 = no cap (default: $LIMIT or 10)",
    )
    parser.add_argument(
        "--output-dir", default=os.environ.get("OUTPUT_DIR"),
        help="where to write scraped folders. Default: a temp dir, deleted "
             "afterwards unless --keep is also given.",
    )
    parser.add_argument(
        "--keep", action="store_true",
        default=os.environ.get("KEEP", "").lower() in ("1", "true", "yes"),
        help="keep the scraped folders on disk even when using the default "
             "temp directory (ignored if --output-dir is given -- those are "
             "always kept).",
    )
    parser.add_argument(
        "--publish", action="store_true",
        default=bool(os.environ.get("OUTPUT_BUCKET")),
        help="also mirror attachments to Cloud Storage (requires OUTPUT_BUCKET "
             "to be set in the environment; see web_scrapers/storage.py). "
             "On by default when OUTPUT_BUCKET is set.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        default=os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"),
        help="scrape and validate against the schema, but do NOT write "
             "anything to BigQuery. Useful for checking a run before it "
             "touches the real database.",
    )
    return parser.parse_args(argv)


def submit_one(record: dict, schema: dict, client, dry_run: bool) -> tuple[str, str]:
    """Validate one record and (unless dry_run) submit it. Returns (action, detail)."""
    import jsonschema
    import validate_and_submit as vas  # ingestion/validate_and_submit.py

    validator = jsonschema.Draft7Validator(schema)
    problems = sorted(validator.iter_errors(record), key=lambda e: e.path)
    if problems:
        summary = "; ".join(f"{list(p.path) or '(root)'}: {p.message}" for p in problems)
        return "invalid", summary

    if dry_run:
        return "valid", "(dry-run: not submitted)"

    from bigquery_client import upsert_tender  

    result = upsert_tender(client, record)
    detail = f"tender_id={result['tender_id']}"
    if result.get("changed_fields"):
        detail += f" ({', '.join(result['changed_fields'])})"
    return result["action"], detail


def run(args) -> int:
    import json

    from web_scrapers.run_scrapers import run_scraper
    from web_scrapers import storage
    import validate_and_submit as vas

    print(f"=== Scraping: sources={args.sources} limit={args.limit} ===")
    result = run_scraper(limit=args.limit, output_dir=args.output_dir, sources=args.sources)
    print(result.summary())
    if args.publish:
        folders, objects = storage.publish(result.output_dir)
        print(f"Published {folders} folder(s), {objects} object(s) to Cloud Storage.")

    if result.total == 0:
        print("\nNothing scraped -- nothing to submit.")
        return 1 if (result.failed or result.skipped) else 0

    schema = vas.load_schema()
    client = None
    if not args.dry_run:
        from bigquery_client import get_client
        client = get_client()

    print(f"\n=== {'Validating' if args.dry_run else 'Submitting'} "
          f"{result.total} tender(s) {'(dry-run)' if args.dry_run else 'to BigQuery'} ===")

    counts = {"inserted": 0, "updated": 0, "noop": 0, "valid": 0, "invalid": 0}
    for tender_dir in result.tender_dirs:
        tender_json = tender_dir / "tender.json"
        try:
            record = json.loads(tender_json.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  [SKIPPED] {tender_dir.name}: could not read tender.json ({exc})")
            counts["invalid"] += 1
            continue

        action, detail = submit_one(record, schema, client, args.dry_run)
        counts[action] = counts.get(action, 0) + 1
        print(f"  [{action.upper()}] {record.get('title', '(no title)')!r} "
              f"[{tender_dir.name}] {detail}")

    print(
        f"\nDone. scraped={result.total} "
        + " ".join(f"{k}={v}" for k, v in counts.items() if v)
    )
    if result.failed:
        print(f"Scraper sources that failed: {result.failed}")
    if result.skipped:
        print(f"Scraper sources that were skipped: {result.skipped}")

    return 1 if (counts.get("invalid") or result.failed) else 0


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.output_dir:
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        return run(args)

    # No --output-dir: use a temp dir. Keep it only if --keep was passed.
    with tempfile.TemporaryDirectory() as temp_dir:
        args.output_dir = temp_dir
        print(f"(using temporary directory: {temp_dir})")
        exit_code = run(args)
        if args.keep:
            print("--keep was set but no --output-dir was given, so there was "
                  "nowhere permanent to keep the files -- pass --output-dir "
                  "next time to keep them.")
        return exit_code


if __name__ == "__main__":
    sys.exit(main())
