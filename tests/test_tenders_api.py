from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
sys.path.insert(0, str(API_ROOT))
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")


from app import runtime_config
from app.config import settings
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
