from __future__ import annotations

from bigquery_client import TENDERS_TABLE, SNAPSHOTS_TABLE, get_client, upsert_tender
from google.api_core.exceptions import BadRequest
from google.cloud import bigquery

BASE_RECORD = {
    "source_id": "test-source",
    "source_reference_id": "TEST-0001",
    "source_url": "https://example.invalid/tenders/TEST-0001",
    "title": "Smoke-test tender — safe to ignore",
    "status": "open",
    "documents": [],
}


def cleanup(client: bigquery.Client, tender_id: str) -> None:
    client.query(
        f"DELETE FROM `{TENDERS_TABLE}` WHERE tender_id = @id",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", tender_id)]
        ),
    ).result()
    try:
        client.query(
            f"DELETE FROM `{SNAPSHOTS_TABLE}` WHERE tender_id = @id",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", tender_id)]
            ),
        ).result()
    except BadRequest as exc:
        if "streaming buffer" in str(exc):
            print(
                "   (note: the test snapshot row couldn't be deleted yet — it's "
                "still in BigQuery's streaming buffer. Harmless and expected; "
                "see the comment in cleanup() if you want the full explanation.)"
            )
        else:
            raise


def main() -> int:
    print("Connecting to BigQuery (project tenderai-dev)...")
    client = get_client()

    print("1. Inserting a fresh test tender...")
    result = upsert_tender(client, BASE_RECORD)
    assert result["action"] == "inserted", f"expected 'inserted', got {result}"
    tender_id = result["tender_id"]
    print(f"   OK — tender_id={tender_id}")

    try:
        print("2. Re-submitting the identical record (should be a no-op)...")
        result = upsert_tender(client, BASE_RECORD)
        assert result["action"] == "noop", f"expected 'noop', got {result}"
        assert result["tender_id"] == tender_id, "no-op returned a different tender_id!"
        print("   OK — no duplicate row created")

        print("3. Submitting a changed record (should update + log a snapshot)...")
        changed = dict(BASE_RECORD, status="closed")
        result = upsert_tender(client, changed)
        assert result["action"] == "updated", f"expected 'updated', got {result}"
        assert result["tender_id"] == tender_id, "update minted a new tender_id!"
        assert "status" in result["changed_fields"]
        print(f"   OK — changed_fields={result['changed_fields']}")

        print("\nAll checks passed. Your setup is ready — see TEAM-SUBMIT-GUIDE.md"
              " to submit real tenders.")
        return 0
    finally:
        print("Cleaning up test row(s)...")
        cleanup(client, tender_id)


if __name__ == "__main__":
    raise SystemExit(main())