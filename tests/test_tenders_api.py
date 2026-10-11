from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import types

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
sys.path.insert(0, str(API_ROOT))
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")


if "firebase_admin" not in sys.modules:
    firebase_admin = types.ModuleType("firebase_admin")
    firebase_admin._apps = [object()]
    firebase_auth = types.ModuleType("firebase_admin.auth")
    firebase_credentials = types.ModuleType("firebase_admin.credentials")
    firebase_firestore = types.ModuleType("firebase_admin.firestore")
    firebase_firestore.client = lambda: None
    firebase_firestore.SERVER_TIMESTAMP = object()
    firebase_admin.auth = firebase_auth
    firebase_admin.credentials = firebase_credentials
    firebase_admin.firestore = firebase_firestore
    sys.modules["firebase_admin"] = firebase_admin
    sys.modules["firebase_admin.auth"] = firebase_auth
    sys.modules["firebase_admin.credentials"] = firebase_credentials
    sys.modules["firebase_admin.firestore"] = firebase_firestore


from app import auth as auth_module
from app import bigquery as bigquery_module
from app import runtime_config
from app.auth import current_user
from app.config import settings
from app.main import app
from app.models import TenderOut, TenderTagOut
from app.routers import tenders


def _row(**updates) -> dict:
    row = {
        "tender_id": "tender-1",
        "title": "Evaluation services",
        "first_seen_at": "2026-01-01T00:00:00Z",
        "last_scanned_at": "2026-01-02T00:00:00Z",
        "updated_at": "2026-01-03T00:00:00Z",
        "documents": [],
    }
    row.update(updates)
    return row


def _get_tenders(**updates) -> list[TenderOut]:
    params = {
        "limit": 50,
        "offset": 0,
        "status": None,
        "category": None,
        "source_id": None,
        "location": None,
        "min_value": None,
        "max_value": None,
        "closing_before": None,
        "closing_after": None,
        "year": None,
        "q": None,
    }
    params.update(updates)
    return tenders.get_tenders(**params)


@pytest.fixture
def api_client():
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_client():
    app.dependency_overrides.clear()
    app.dependency_overrides[current_user] = lambda: {
        "uid": "test-user",
        "email": "user@example.com",
        "isAdmin": False,
    }
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "value",
    [None, {}, "{}", "null", "", "not-json", [], "[]", 5, "5"],
)
def test_classification_tags_returns_empty_for_null_empty_or_non_object_input(value):
    assert tenders._classification_tags(value, {"known": "Known"}) == []


def test_classification_tags_supports_dict_and_orders_by_score_then_id():
    labels = {
        "alpha": "Alpha",
        "beta": "Beta",
        "highest": "Highest",
    }

    assert tenders._classification_tags(
        {"beta": 3, "highest": 5, "alpha": 3},
        labels,
    ) == [
        {"id": "highest", "label": "Highest"},
        {"id": "alpha", "label": "Alpha"},
        {"id": "beta", "label": "Beta"},
    ]


def test_classification_tags_supports_json_strings_and_excludes_unknown_ids():
    value = json.dumps({"unknown": 5, "known": 4})

    assert tenders._classification_tags(value, {"known": "Known label"}) == [
        {"id": "known", "label": "Known label"},
    ]


def test_classification_tags_ignores_invalid_tags_and_scores():
    value = {
        "valid": 2.5,
        "boolean": True,
        "string": "5",
        "low": 0,
        "high": 6,
        "nan": float("nan"),
        "infinite": float("inf"),
        "oversized": 10**1000,
        "": 5,
        123: 5,
    }
    labels = {tag_id: str(tag_id) for tag_id in value}

    assert tenders._classification_tags(value, labels) == [
        {"id": "valid", "label": "valid"},
    ]


def test_tender_output_uses_separate_taxonomies_and_never_serializes_scores():
    taxonomies = runtime_config.RuntimeTaxonomies(
        focus_areas={"shared": "Shared focus"},
        work_types={"shared": "Shared work"},
        generation="41",
    )

    output = tenders._tender_outputs(
        [_row(focus_areas={"shared": 5}, work_types='{"shared": 4}')],
        taxonomies,
    )[0].model_dump(mode="json")

    assert output["focus_areas"] == [{"id": "shared", "label": "Shared focus"}]
    assert output["work_types"] == [{"id": "shared", "label": "Shared work"}]
    assert "score" not in json.dumps(output)
    assert set(TenderTagOut.model_json_schema()["properties"]) == {"id", "label"}


def test_tender_out_defaults_classifications_for_legacy_records():
    output = TenderOut(**_row())

    assert output.focus_areas == []
    assert output.work_types == []


def test_bigquery_path_loads_taxonomies_once_for_non_empty_result(monkeypatch):
    rows = [
        _row(tender_id="one", focus_areas={"focus": 5}, work_types={"work": 3}),
        _row(tender_id="two", focus_areas='{"focus": 2}', work_types=None),
    ]
    downloads = 0

    def get_taxonomy_labels():
        nonlocal downloads
        downloads += 1
        return runtime_config.RuntimeTaxonomies(
            focus_areas={"focus": "Focus label"},
            work_types={"work": "Work label"},
            generation="41",
        )

    monkeypatch.setattr(settings, "use_mock_data", False)
    monkeypatch.setattr(tenders, "get_client", lambda: object())
    monkeypatch.setattr(tenders, "list_tenders", lambda *args, **kwargs: rows)
    monkeypatch.setattr(runtime_config, "get_taxonomy_labels", get_taxonomy_labels)

    outputs = _get_tenders()

    assert downloads == 1
    assert outputs[0].focus_areas[0].label == "Focus label"
    assert outputs[0].work_types[0].label == "Work label"
    assert outputs[1].focus_areas[0].label == "Focus label"
    assert outputs[1].work_types == []


def test_bigquery_empty_result_does_not_load_taxonomies(monkeypatch):
    monkeypatch.setattr(settings, "use_mock_data", False)
    monkeypatch.setattr(tenders, "get_client", lambda: object())
    monkeypatch.setattr(tenders, "list_tenders", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        runtime_config,
        "get_taxonomy_labels",
        lambda: pytest.fail("taxonomy should not be loaded for an empty result"),
    )

    assert _get_tenders() == []


def test_runtime_taxonomy_failure_returns_empty_classifications(monkeypatch, caplog):
    monkeypatch.setattr(settings, "use_mock_data", False)
    monkeypatch.setattr(tenders, "get_client", lambda: object())
    monkeypatch.setattr(
        tenders,
        "list_tenders",
        lambda *args, **kwargs: [_row(focus_areas={"focus": 5}, work_types={"work": 4})],
    )

    def unavailable():
        raise runtime_config.RuntimeConfigUnavailable("sensitive internal detail")

    monkeypatch.setattr(runtime_config, "get_taxonomy_labels", unavailable)

    output = _get_tenders()[0]

    assert output.focus_areas == []
    assert output.work_types == []
    assert "Runtime taxonomy unavailable" in caplog.text
    assert "sensitive internal detail" not in caplog.text


def test_mock_path_returns_empty_arrays_without_loading_runtime_taxonomy(monkeypatch):
    monkeypatch.setattr(settings, "use_mock_data", True)
    monkeypatch.setattr(
        runtime_config,
        "get_taxonomy_labels",
        lambda: pytest.fail("mock mode must not load runtime taxonomy"),
    )

    outputs = _get_tenders()

    assert outputs
    assert all(output.focus_areas == [] for output in outputs)
    assert all(output.work_types == [] for output in outputs)


def test_get_tenders_route_serializes_public_tags_and_preserves_existing_fields(
    authenticated_client,
    monkeypatch,
):
    oversized = 10**1000
    rows = [
        _row(
            source_reference_id="REF-42",
            source_id="test-portal",
            source_url="https://example.com/tender/42",
            issuing_agency="Test Agency",
            category="tender",
            status="open",
            publish_date="2026-02-01",
            closing_date="2026-03-01",
            value_amount=125000,
            value_currency="AUD",
            value_notes="Estimated value",
            location="Perth",
            description="Evaluation and policy design services.",
            summary_headline="Evaluation services required.",
            focus_areas=json.dumps(
                {
                    "beta": 3,
                    "highest": 5,
                    "alpha": 3,
                    "shared": 2,
                    "unknown": 5,
                    "oversized": oversized,
                }
            ),
            work_types={"shared": 5, "work_b": 3},
            contact_name="Example Contact",
            contact_email="contact@example.com",
            contact_phone="+61 8 0000 0000",
            lodgment_address="Online",
            documents=[
                {
                    "document_id": "doc-1",
                    "file_name": "specification.pdf",
                    "file_type": "pdf",
                    "storage_uri": "gs://tenderai-documents/specification.pdf",
                }
            ],
            content_hash="hash-42",
            raw_extra='{"portal_status": "Open"}',
            distance=0.125,
        )
    ]
    captured: dict = {}
    taxonomy_calls = 0

    def list_tenders(_client, **kwargs):
        captured.update(kwargs)
        return rows

    def get_taxonomy_labels():
        nonlocal taxonomy_calls
        taxonomy_calls += 1
        return runtime_config.RuntimeTaxonomies(
            focus_areas={
                "alpha": "Alpha focus",
                "beta": "Beta focus",
                "highest": "Highest focus",
                "shared": "Shared focus",
            },
            work_types={
                "shared": "Shared work",
                "work_b": "Work B",
            },
            generation="41",
        )

    monkeypatch.setattr(settings, "use_mock_data", False)
    monkeypatch.setattr(tenders, "get_client", lambda: object())
    monkeypatch.setattr(tenders, "list_tenders", list_tenders)
    monkeypatch.setattr(runtime_config, "get_taxonomy_labels", get_taxonomy_labels)

    response = authenticated_client.get(
        "/tenders",
        params={
            "limit": 7,
            "offset": 2,
            "status": "open",
            "category": "tender",
            "source_id": "test-portal",
            "location": "Perth",
            "min_value": 100,
            "max_value": 200000,
            "closing_before": "2026-12-31",
            "closing_after": "2026-01-01",
            "year": "2026",
            "q": "evaluation",
        },
    )

    assert response.status_code == 200
    assert taxonomy_calls == 1
    assert captured == {
        "limit": 7,
        "offset": 2,
        "status": "open",
        "category": "tender",
        "source_id": "test-portal",
        "location": "Perth",
        "min_value": 100.0,
        "max_value": 200000.0,
        "closing_before": bigquery_module.date(2026, 12, 31),
        "closing_after": bigquery_module.date(2026, 1, 1),
        "year": "2026",
        "q": "evaluation",
    }

    body = response.json()[0]
    assert set(body) == set(TenderOut.model_fields)
    assert body["tender_id"] == "tender-1"
    assert body["source_reference_id"] == "REF-42"
    assert body["title"] == "Evaluation services"
    assert body["issuing_agency"] == "Test Agency"
    assert body["distance"] == 0.125
    assert body["raw_extra"] == {"portal_status": "Open"}
    assert body["documents"] == [
        {
            "document_id": "doc-1",
            "file_name": "specification.pdf",
            "file_type": "pdf",
            "extracted_text": None,
            "parsed_at": None,
            "storage_url": "https://storage.googleapis.com/tenderai-documents/specification.pdf",
        }
    ]
    assert body["focus_areas"] == [
        {"id": "highest", "label": "Highest focus"},
        {"id": "alpha", "label": "Alpha focus"},
        {"id": "beta", "label": "Beta focus"},
        {"id": "shared", "label": "Shared focus"},
    ]
    assert body["work_types"] == [
        {"id": "shared", "label": "Shared work"},
        {"id": "work_b", "label": "Work B"},
    ]
    assert all(set(tag) == {"id", "label"} for tag in body["focus_areas"])
    assert all(set(tag) == {"id", "label"} for tag in body["work_types"])
    assert "unknown" not in json.dumps(body)
    assert "oversized" not in json.dumps(body)


def test_get_tenders_route_handles_missing_and_empty_results(
    authenticated_client,
    monkeypatch,
):
    results = [[_row()], []]
    monkeypatch.setattr(settings, "use_mock_data", False)
    monkeypatch.setattr(tenders, "get_client", lambda: object())
    monkeypatch.setattr(tenders, "list_tenders", lambda *args, **kwargs: results.pop(0))
    monkeypatch.setattr(
        runtime_config,
        "get_taxonomy_labels",
        lambda: runtime_config.RuntimeTaxonomies({}, {}, "41"),
    )

    legacy_response = authenticated_client.get("/tenders")
    empty_response = authenticated_client.get("/tenders")

    assert legacy_response.status_code == 200
    assert legacy_response.json()[0]["focus_areas"] == []
    assert legacy_response.json()[0]["work_types"] == []
    assert empty_response.status_code == 200
    assert empty_response.json() == []


def test_get_tenders_route_runtime_failure_preserves_response(
    authenticated_client,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(settings, "use_mock_data", False)
    monkeypatch.setattr(tenders, "get_client", lambda: object())
    monkeypatch.setattr(
        tenders,
        "list_tenders",
        lambda *args, **kwargs: [
            _row(focus_areas={"secret_focus": 5}, work_types={"secret_work": 4})
        ],
    )

    def unavailable():
        raise runtime_config.RuntimeConfigUnavailable("secret configuration detail")

    monkeypatch.setattr(runtime_config, "get_taxonomy_labels", unavailable)

    response = authenticated_client.get("/tenders")

    assert response.status_code == 200
    assert response.json()[0]["tender_id"] == "tender-1"
    assert response.json()[0]["focus_areas"] == []
    assert response.json()[0]["work_types"] == []
    assert "secret_focus" not in response.text
    assert "secret_work" not in response.text
    assert "secret configuration detail" not in caplog.text


def test_mock_route_preserves_filters_search_and_pagination_without_gcs(
    authenticated_client,
    monkeypatch,
):
    monkeypatch.setattr(settings, "use_mock_data", True)
    monkeypatch.setattr(
        runtime_config,
        "get_taxonomy_labels",
        lambda: pytest.fail("mock mode must not load runtime taxonomy"),
    )

    filtered = authenticated_client.get(
        "/tenders",
        params={
            "limit": 1,
            "offset": 0,
            "status": "open",
            "location": "Victoria",
            "q": "evaluation",
        },
    )
    paged = authenticated_client.get(
        "/tenders",
        params={"limit": 1, "offset": 1, "status": "open"},
    )

    assert filtered.status_code == 200
    assert filtered.json()[0]["tender_id"] == "mock-1"
    assert filtered.json()[0]["focus_areas"] == []
    assert filtered.json()[0]["work_types"] == []
    assert paged.status_code == 200
    assert paged.json()[0]["tender_id"] == "mock-2"


def test_tenders_route_enforces_authentication(api_client, monkeypatch):
    monkeypatch.setattr(settings, "use_mock_data", False)
    monkeypatch.setattr(
        tenders,
        "get_client",
        lambda: pytest.fail("unauthenticated requests must not execute the endpoint"),
    )

    missing = api_client.get("/tenders")

    def reject_token(_token):
        raise ValueError("invalid test token")

    monkeypatch.setattr(auth_module.fb_auth, "verify_id_token", reject_token, raising=False)
    invalid = api_client.get(
        "/tenders",
        headers={"Authorization": "Bearer invalid-test-token"},
    )

    assert missing.status_code == 401
    assert missing.json()["detail"] == "missing bearer token"
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "invalid token"


class _FakeQueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class _FakeBigQueryClient:
    def __init__(self, rows):
        self._rows = rows
        self.query_text = ""
        self.job_config = None

    def query(self, query, *, job_config):
        self.query_text = query
        self.job_config = job_config
        return _FakeQueryJob(self._rows)


def test_standard_tender_query_includes_classifications_and_preserves_query_behaviour():
    client = _FakeBigQueryClient(
        [
            {
                "tender_id": "sql-standard",
                "documents": [{"file_name": "file.pdf"}],
                "raw_extra": '{"source": "fixture"}',
                "focus_areas": '{"focus": 5}',
                "work_types": {"work": 4},
            }
        ]
    )

    rows = bigquery_module.list_tenders(
        client,
        limit=12,
        offset=3,
        status="open",
        category="tender",
        source_id="portal",
        location="Perth",
        min_value=100,
        max_value=200000,
        closing_before=bigquery_module.date(2026, 12, 31),
        closing_after=bigquery_module.date(2026, 1, 1),
        year="2026",
    )

    sql = client.query_text
    assert "SELECT tender_id" in sql
    assert "focus_areas" in sql
    assert "work_types" in sql
    assert "base.focus_areas" not in sql
    assert "VECTOR_SEARCH" not in sql
    assert "status = @status" in sql
    assert "category = @category" in sql
    assert "source_id = @source_id" in sql
    assert "LOWER(location) LIKE @location" in sql
    assert "value_amount >= @min_value" in sql
    assert "value_amount <= @max_value" in sql
    assert "closing_date <= @closing_before" in sql
    assert "closing_date >= @closing_after" in sql
    assert "EXTRACT(YEAR FROM closing_date) = @year" in sql
    assert "ORDER BY first_seen_at DESC" in sql
    assert "LIMIT @limit OFFSET @offset" in sql
    assert [parameter.name for parameter in client.job_config.query_parameters] == [
        "limit",
        "offset",
        "status",
        "category",
        "source_id",
        "location",
        "min_value",
        "max_value",
        "closing_before",
        "closing_after",
        "year",
    ]
    assert rows[0]["distance"] is None
    assert rows[0]["raw_extra"] == {"source": "fixture"}
    assert rows[0]["documents"] == [{"file_name": "file.pdf"}]
    assert rows[0]["focus_areas"] == '{"focus": 5}'
    assert rows[0]["work_types"] == {"work": 4}


def test_vector_tender_query_includes_classifications_and_preserves_search_behaviour(
    monkeypatch,
):
    client = _FakeBigQueryClient(
        [
            {
                "tender_id": "sql-vector",
                "documents": [],
                "raw_extra": None,
                "focus_areas": {"focus": 5},
                "work_types": '{"work": 4}',
                "distance": 0.25,
            }
        ]
    )
    monkeypatch.setattr(
        bigquery_module,
        "generate_query_embedding",
        lambda query: [0.0] * 768,
    )

    rows = bigquery_module.list_tenders(
        client,
        limit=40,
        offset=10,
        status="open",
        location="Perth",
        closing_before=bigquery_module.date(2026, 12, 31),
        closing_after=bigquery_module.date(2026, 1, 1),
        year="2026",
        q="evaluation",
    )

    sql = client.query_text
    assert "base.focus_areas" in sql
    assert "base.work_types" in sql
    assert "VECTOR_SEARCH" in sql
    assert "base.status = @status" in sql
    assert "LOWER(base.location) LIKE @location" in sql
    assert "base.closing_date <= @closing_before" in sql
    assert "base.closing_date >= @closing_after" in sql
    assert "EXTRACT(YEAR FROM base.closing_date) = @year" in sql
    assert "ORDER BY distance ASC" in sql
    assert "LIMIT @limit OFFSET @offset" in sql
    assert [parameter.name for parameter in client.job_config.query_parameters] == [
        "limit",
        "offset",
        "status",
        "location",
        "closing_before",
        "closing_after",
        "year",
        "query_vector",
        "top_k",
    ]
    top_k = next(
        parameter.value
        for parameter in client.job_config.query_parameters
        if parameter.name == "top_k"
    )
    assert top_k == 250
    assert rows[0]["distance"] == 0.25
    assert rows[0]["focus_areas"] == {"focus": 5}
    assert rows[0]["work_types"] == '{"work": 4}'
