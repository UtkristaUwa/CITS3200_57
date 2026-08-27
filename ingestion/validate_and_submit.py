#!/usr/bin/env python3
"""
validate_and_submit.py — submit formatted tenders to BigQuery.

Usage:
    python validate_and_submit.py path/to/tenders.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

from bigquery_client import get_client, upsert_tender

SCHEMA_PATH = Path(__file__).parent / "tender.schema.json"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_records(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def validate(records: list[dict], schema: dict) -> tuple[list[dict], list[tuple[int, str]]]:
    validator = jsonschema.Draft7Validator(schema)
    valid, errors = [], []
    for i, record in enumerate(records):
        problems = sorted(validator.iter_errors(record), key=lambda e: e.path)
        if problems:
            summary = "; ".join(f"{list(p.path) or '(root)'}: {p.message}" for p in problems)
            errors.append((i, summary))
        else:
            valid.append(record)
    return valid, errors


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} path/to/tenders.json", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"No such file: {path}", file=sys.stderr)
        return 2

    schema = load_schema()
    records = load_records(path)
    print(f"Loaded {len(records)} record(s) from {path}")

    valid, errors = validate(records, schema)
    for index, message in errors:
        print(f"  [SKIPPED] record {index}: {message}")

    if not valid:
        print("Nothing valid to submit.")
        return 1 if errors else 0

    print(f"Submitting {len(valid)} valid record(s) to BigQuery (project tenderai-dev)...")
    client = get_client()

    counts = {"inserted": 0, "updated": 0, "noop": 0}
    for record in valid:
        result = upsert_tender(client, record)
        counts[result["action"]] += 1
        detail = f" ({', '.join(result['changed_fields'])})" if result.get("changed_fields") else ""
        print(f"  [{result['action'].upper()}] {record.get('title', '(no title)')!r} "
              f"-> tender_id={result['tender_id']}{detail}")

    print(
        f"\nDone. inserted={counts['inserted']} updated={counts['updated']} "
        f"noop={counts['noop']} skipped={len(errors)}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
