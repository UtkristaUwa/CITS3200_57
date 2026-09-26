from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import sys
import types

import pytest
from fastapi.testclient import TestClient
from google.api_core import exceptions as google_exceptions
from google.auth import exceptions as google_auth_exceptions


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
sys.path.insert(0, str(API_ROOT))
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")


# The repository's API image installs firebase-admin, but the shared local test
# virtualenv may not. Supply only the import surface needed to construct the app;
# all authentication calls are replaced through FastAPI dependency overrides.
if importlib.util.find_spec("firebase_admin") is None:
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

from app import runtime_config
from app.auth import current_user
from app.config import settings
from app.main import app


CFG = """# file header
[models]
# model comment
triage_model = gemini-triage-old
extraction_model = gemini-extraction-old
triage_temperature = 0.1

[taxonomies]
tags = construction, consulting

[system_prompts]
summary = Keep this prompt exactly as it is.
    Including this continuation line.
"""


@dataclass
class FakeBlob:
    content: bytes
    generation: int = 41
    missing: bool = False
    fail_download: bool = False
    fail_upload: bool = False
    race_on_download: bool = False

    def reload(self):
        if self.missing:
            raise google_exceptions.NotFound("missing")

    def download_as_bytes(self, *, if_generation_match):
        if self.fail_download:
            raise google_exceptions.ServiceUnavailable("unavailable")
        if self.race_on_download:
            self.generation += 1
            self.race_on_download = False
        if if_generation_match != self.generation:
            raise google_exceptions.PreconditionFailed("changed")
        return self.content

    def upload_from_string(self, data, *, content_type, if_generation_match):
        assert content_type == "text/plain; charset=utf-8"
        if self.fail_upload:
            raise google_exceptions.ServiceUnavailable("unavailable")
        if if_generation_match != self.generation:
            raise google_exceptions.PreconditionFailed("changed")
        self.content = data
        self.generation += 1


class FakeBucket:
    def __init__(self, blob: FakeBlob):
        self._blob = blob

    def blob(self, object_name):
        assert object_name == "runtime/tender_processor.cfg"
        return self._blob


class FakeStorageClient:
    def __init__(self, blob: FakeBlob):
        self._blob = blob

    def bucket(self, bucket_name):
        assert bucket_name == "test-runtime-config"
        return FakeBucket(self._blob)


@pytest.fixture
def blob(monkeypatch) -> FakeBlob:
    fake_blob = FakeBlob(CFG.encode("utf-8"))
    monkeypatch.setattr(settings, "runtime_config_bucket", "test-runtime-config")
    monkeypatch.setattr(settings, "runtime_config_object", "runtime/tender_processor.cfg")
    monkeypatch.setattr(
        runtime_config,
        "get_storage_client",
        lambda: FakeStorageClient(fake_blob),
    )
    return fake_blob


@pytest.fixture
def client():
    app.dependency_overrides[current_user] = lambda: {
        "uid": "admin-1",
        "isAdmin": True,
    }
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_get_returns_only_models_and_generation(client, blob):
    response = client.get("/admin/config/models")

    assert response.status_code == 200
    assert response.json() == {
        "triage_model": "gemini-triage-old",
        "extraction_model": "gemini-extraction-old",
        "generation": "41",
    }
    assert "prompt" not in response.text
    assert "taxonomy" not in response.text


def test_patch_triage_only_preserves_all_unrelated_bytes(client, blob):
    response = client.patch(
        "/admin/config/models",
        json={"triage_model": " gemini-triage-new ", "generation": "41"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "triage_model": "gemini-triage-new",
        "extraction_model": "gemini-extraction-old",
        "generation": "42",
    }
    expected = CFG.replace(
        "triage_model = gemini-triage-old",
        "triage_model = gemini-triage-new",
        1,
    )
    assert blob.content == expected.encode("utf-8")


def test_patch_extraction_only(client, blob):
    response = client.patch(
        "/admin/config/models",
        json={"extraction_model": "gemini-extraction-new", "generation": "41"},
    )

    assert response.status_code == 200
    assert response.json()["triage_model"] == "gemini-triage-old"
    assert response.json()["extraction_model"] == "gemini-extraction-new"
    assert b"summary = Keep this prompt exactly as it is." in blob.content


def test_patch_both_models(client, blob):
    response = client.patch(
        "/admin/config/models",
        json={
            "triage_model": "gemini-triage-new",
            "extraction_model": "gemini-extraction-new",
            "generation": "41",
        },
    )

    assert response.status_code == 200
    assert response.json()["triage_model"] == "gemini-triage-new"
    assert response.json()["extraction_model"] == "gemini-extraction-new"


@pytest.mark.parametrize("invalid", ["", "   ", "model\nname", "model\rname", "model\x01name"])
def test_invalid_model_ids_return_422(client, blob, invalid):
    response = client.patch(
        "/admin/config/models",
        json={"triage_model": invalid, "generation": "41"},
    )

    assert response.status_code == 422
    assert blob.content == CFG.encode("utf-8")


def test_patch_rejects_no_update_and_unknown_fields(client, blob):
    no_update = client.patch("/admin/config/models", json={"generation": "41"})
    unknown = client.patch(
        "/admin/config/models",
        json={"triage_model": "valid", "generation": "41", "prompt": "replace me"},
    )

    assert no_update.status_code == 422
    assert unknown.status_code == 422
    assert blob.content == CFG.encode("utf-8")


def test_non_admin_is_forbidden(blob):
    app.dependency_overrides[current_user] = lambda: {
        "uid": "user-1",
        "isAdmin": False,
    }
    try:
        with TestClient(app) as test_client:
            get_response = test_client.get("/admin/config/models")
            patch_response = test_client.patch(
                "/admin/config/models",
                json={"triage_model": "valid", "generation": "41"},
            )
    finally:
        app.dependency_overrides.clear()

    assert get_response.status_code == 403
    assert patch_response.status_code == 403


def test_unauthenticated_request_is_rejected(blob):
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        response = test_client.get("/admin/config/models")

    assert response.status_code == 401


def test_cors_preflight_allows_patch(client, blob):
    response = client.options(
        "/admin/config/models",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
        },
    )

    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]


def test_stale_generation_returns_409(client, blob):
    response = client.patch(
        "/admin/config/models",
        json={"triage_model": "gemini-new", "generation": "40"},
    )

    assert response.status_code == 409
    assert blob.content == CFG.encode("utf-8")


def test_upload_race_returns_409(client, blob, monkeypatch):
    original_upload = blob.upload_from_string

    def race(data, *, content_type, if_generation_match):
        blob.generation += 1
        return original_upload(
            data,
            content_type=content_type,
            if_generation_match=if_generation_match,
        )

    monkeypatch.setattr(blob, "upload_from_string", race)
    response = client.patch(
        "/admin/config/models",
        json={"triage_model": "gemini-new", "generation": "41"},
    )

    assert response.status_code == 409
    assert blob.content == CFG.encode("utf-8")


def test_download_race_during_patch_returns_409(client, blob):
    blob.race_on_download = True

    response = client.patch(
        "/admin/config/models",
        json={"triage_model": "gemini-new", "generation": "41"},
    )

    assert response.status_code == 409
    assert blob.content == CFG.encode("utf-8")


def test_missing_settings_return_503(client, blob, monkeypatch):
    monkeypatch.setattr(settings, "runtime_config_bucket", "")

    response = client.get("/admin/config/models")

    assert response.status_code == 503
    assert response.json()["detail"] == "runtime model configuration is not configured"


def test_missing_object_returns_503(client, blob):
    blob.missing = True

    response = client.get("/admin/config/models")

    assert response.status_code == 503
    assert "not initialized" in response.json()["detail"]


def test_storage_client_initialization_failure_returns_503(client, blob, monkeypatch):
    def fail_to_create_client():
        raise google_auth_exceptions.DefaultCredentialsError("credentials unavailable")

    monkeypatch.setattr(runtime_config, "get_storage_client", fail_to_create_client)

    response = client.get("/admin/config/models")

    assert response.status_code == 503


def test_download_failure_returns_503(client, blob):
    blob.fail_download = True

    response = client.get("/admin/config/models")

    assert response.status_code == 503


@pytest.mark.parametrize(
    "malformed",
    [
        "[taxonomies]\ntags = consulting\n",
        "[models]\ntriage_model = only-one-key\n",
        (
            "[models]\ntriage_model = first\ntriage_model = duplicate\n"
            "extraction_model = extraction\n"
        ),
        (
            "[models]\ntriage_model = real-key\n"
            "    unexpected continuation\n"
            "extraction_model = extraction\n"
        ),
    ],
)
def test_malformed_cfg_returns_503(client, blob, malformed):
    blob.content = malformed.encode("utf-8")

    response = client.get("/admin/config/models")

    assert response.status_code == 503


def test_indented_continuation_cannot_be_used_as_a_model_key(client, blob):
    blob.content = (
        "[models]\n"
        "other = ordinary value\n"
        "    triage_model = continuation-not-a-real-key\n"
        "extraction_model = gemini-extraction\n"
    ).encode("utf-8")

    response = client.get("/admin/config/models")

    assert response.status_code == 503


def test_programming_errors_are_not_hidden_as_503(client, blob, monkeypatch):
    monkeypatch.setattr(runtime_config, "get_storage_client", lambda: object())

    with pytest.raises(AttributeError):
        client.get("/admin/config/models")


def test_upload_failure_returns_503_without_changing_content(client, blob):
    blob.fail_upload = True

    response = client.patch(
        "/admin/config/models",
        json={"extraction_model": "gemini-new", "generation": "41"},
    )

    assert response.status_code == 503
    assert blob.content == CFG.encode("utf-8")


@pytest.mark.parametrize("invalid", ["model\u2028name", "model\u2029name", "model\u200ename"])
def test_unicode_line_and_format_characters_return_422(client, blob, invalid):
    response = client.patch(
        "/admin/config/models",
        json={"triage_model": invalid, "generation": "41"},
    )

    assert response.status_code == 422
    assert blob.content == CFG.encode("utf-8")


def test_model_identifier_longer_than_200_characters_returns_422(client, blob):
    response = client.patch(
        "/admin/config/models",
        json={"triage_model": "m" * 201, "generation": "41"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("generation", ["", "abc", "-1", " 41", 41])
def test_invalid_generation_returns_422(client, blob, generation):
    response = client.patch(
        "/admin/config/models",
        json={"triage_model": "gemini-new", "generation": generation},
    )

    assert response.status_code == 422


def test_missing_generation_returns_422(client, blob):
    response = client.patch(
        "/admin/config/models",
        json={"triage_model": "gemini-new"},
    )

    assert response.status_code == 422


def test_repository_tender_processor_cfg_is_compatible():
    text = (REPO_ROOT / "processing" / "tender_processor.cfg").read_text(encoding="utf-8")

    models = runtime_config._parse_models(text)

    assert models == {
        "triage_model": "gemini-2.5-flash",
        "extraction_model": "gemini-2.5-flash",
    }


def test_crlf_is_preserved(client, blob):
    original = CFG.replace("\n", "\r\n")
    blob.content = original.encode("utf-8")

    response = client.patch(
        "/admin/config/models",
        json={"triage_model": "gemini-new", "generation": "41"},
    )

    assert response.status_code == 200
    expected = original.replace("triage_model = gemini-triage-old", "triage_model = gemini-new", 1)
    assert blob.content == expected.encode("utf-8")


def test_no_final_newline_is_preserved(client, blob):
    original = CFG.rstrip("\n")
    blob.content = original.encode("utf-8")

    response = client.patch(
        "/admin/config/models",
        json={"extraction_model": "gemini-new", "generation": "41"},
    )

    assert response.status_code == 200
    expected = original.replace(
        "extraction_model = gemini-extraction-old",
        "extraction_model = gemini-new",
        1,
    )
    assert blob.content == expected.encode("utf-8")
    assert not blob.content.endswith(b"\n")
