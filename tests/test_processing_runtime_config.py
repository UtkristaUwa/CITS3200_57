import importlib
import sys
from pathlib import Path

import pytest
from google.api_core import exceptions as google_exceptions
from google.auth import exceptions as google_auth_exceptions
from google import genai

from processing import runtime_config


VALID_CFG = b"""[models]
triage_model = gemini-triage
extraction_model = gemini-extraction
triage_temperature = 0.1
summary_temperature = 0.2
extraction_temperature = 0.1
triage_char_limit = 6000

[taxonomies]
tags = consulting

[system_prompts]
doc_triage = Triage documents.
summary = Summarise tenders.
field_extraction = Extract fields.

[field_descriptions]
summary_headline = Headline.

[prompt_templates]
triage_user_prompt = Filename: {file_name}
"""


class FakeBlob:
    def __init__(self, data=VALID_CFG, error=None):
        self.data = data
        self.error = error

    def download_as_bytes(self):
        if self.error:
            raise self.error
        return self.data


class FakeBucket:
    def __init__(self, blob):
        self._blob = blob

    def blob(self, object_name):
        assert object_name == "runtime/tender_processor.cfg"
        return self._blob


class FakeClient:
    def __init__(self, blob):
        self._blob = blob

    def bucket(self, bucket_name):
        assert bucket_name == "runtime-config-bucket"
        return FakeBucket(self._blob)


@pytest.fixture(autouse=True)
def clean_runtime_environment(monkeypatch):
    monkeypatch.delenv(runtime_config.RUNTIME_CONFIG_BUCKET, raising=False)
    monkeypatch.delenv(runtime_config.RUNTIME_CONFIG_OBJECT, raising=False)
    monkeypatch.delenv(runtime_config.TENDER_PROCESSOR_CONFIG, raising=False)


def configure_runtime(monkeypatch, blob):
    monkeypatch.setenv(runtime_config.RUNTIME_CONFIG_BUCKET, "runtime-config-bucket")
    monkeypatch.setenv(runtime_config.RUNTIME_CONFIG_OBJECT, "runtime/tender_processor.cfg")
    monkeypatch.setattr(runtime_config.storage, "Client", lambda: FakeClient(blob))


def assert_fallback(result):
    assert result.active is False
    assert result.path is None
    assert runtime_config.TENDER_PROCESSOR_CONFIG not in runtime_config.os.environ


def test_missing_environment_uses_repository_fallback_and_clears_stale_path(tmp_path, monkeypatch):
    monkeypatch.setenv(runtime_config.TENDER_PROCESSOR_CONFIG, "/tmp/stale-invalid.cfg")

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert "not both configured" in result.reason


def test_success_downloads_valid_cfg_and_selects_persistent_local_file(tmp_path, monkeypatch):
    configure_runtime(monkeypatch, FakeBlob())

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert result.active is True
    assert result.path is not None
    assert Path(result.path).read_bytes() == VALID_CFG
    assert runtime_config.os.environ[runtime_config.TENDER_PROCESSOR_CONFIG] == result.path
    assert Path(result.path).is_file()


def test_object_not_found_uses_fallback(tmp_path, monkeypatch):
    configure_runtime(monkeypatch, FakeBlob(error=google_exceptions.NotFound("missing")))

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert "not found" in result.reason


def test_storage_client_auth_initialization_failure_uses_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv(runtime_config.RUNTIME_CONFIG_BUCKET, "runtime-config-bucket")
    monkeypatch.setenv(runtime_config.RUNTIME_CONFIG_OBJECT, "runtime/tender_processor.cfg")

    def fail_client():
        raise google_auth_exceptions.DefaultCredentialsError("credentials unavailable")

    monkeypatch.setattr(runtime_config.storage, "Client", fail_client)

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert "DefaultCredentialsError" in result.reason


def test_download_service_unavailable_uses_fallback(tmp_path, monkeypatch):
    configure_runtime(
        monkeypatch,
        FakeBlob(error=google_exceptions.ServiceUnavailable("service unavailable")),
    )

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert "ServiceUnavailable" in result.reason


def test_malformed_cfg_uses_fallback(tmp_path, monkeypatch):
    configure_runtime(monkeypatch, FakeBlob(b"[models\ntriage_model = broken"))

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert "invalid CFG syntax" in result.reason


def test_invalid_utf8_uses_repository_fallback(tmp_path, monkeypatch):
    configure_runtime(monkeypatch, FakeBlob(b"\xff\xfe\x00invalid runtime config"))

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert "not valid UTF-8" in result.reason


@pytest.mark.parametrize(
    ("key", "valid_value", "invalid_value"),
    [
        ("triage_temperature", "0.1", "not-a-float"),
        ("summary_temperature", "0.2", "not-a-float"),
        ("extraction_temperature", "0.1", "not-a-float"),
        ("triage_char_limit", "6000", "not-an-int"),
    ],
)
def test_invalid_numeric_model_setting_uses_repository_fallback(
    tmp_path,
    monkeypatch,
    key,
    valid_value,
    invalid_value,
):
    data = VALID_CFG.replace(
        f"{key} = {valid_value}".encode(),
        f"{key} = {invalid_value}".encode(),
    )
    configure_runtime(monkeypatch, FakeBlob(data))

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert f"invalid numeric models.{key}" in result.reason


@pytest.mark.parametrize(
    ("data", "expected_reason"),
    [
        (
            VALID_CFG.replace(b"[models]\n", b"[other]\n"),
            "missing required section(s): models",
        ),
        (
            VALID_CFG.replace(b"triage_model = gemini-triage\n", b""),
            "missing models.triage_model",
        ),
        (
            VALID_CFG.replace(b"extraction_model = gemini-extraction\n", b""),
            "missing models.extraction_model",
        ),
        (
            VALID_CFG.replace(b"triage_model = gemini-triage", b"triage_model =   "),
            "empty models.triage_model",
        ),
        (
            VALID_CFG.replace(b"extraction_model = gemini-extraction", b"extraction_model =   "),
            "empty models.extraction_model",
        ),
    ],
)
def test_invalid_model_structure_uses_fallback(
    tmp_path,
    monkeypatch,
    data,
    expected_reason,
):
    configure_runtime(monkeypatch, FakeBlob(data))

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert expected_reason in result.reason


def test_missing_non_model_required_section_uses_fallback(tmp_path, monkeypatch):
    data = VALID_CFG.replace(b"[prompt_templates]", b"[not_prompt_templates]")
    configure_runtime(monkeypatch, FakeBlob(data))

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert "prompt_templates" in result.reason


def test_local_file_write_failure_uses_fallback(tmp_path, monkeypatch):
    configure_runtime(monkeypatch, FakeBlob())

    def fail_write(_path, _data):
        raise OSError("disk is read-only")

    monkeypatch.setattr(Path, "write_bytes", fail_write)

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert "disk is read-only" in result.reason


def test_repository_cfg_remains_unchanged_when_runtime_preparation_fails(tmp_path):
    repository_cfg = Path(__file__).resolve().parents[1] / "processing" / "tender_processor.cfg"
    before = repository_cfg.read_bytes()

    result = runtime_config.prepare_runtime_config(tmp_path)

    assert_fallback(result)
    assert repository_cfg.read_bytes() == before


def test_repository_cfg_is_structurally_compatible():
    repository_cfg = Path(__file__).resolve().parents[1] / "processing" / "tender_processor.cfg"

    runtime_config._validate_config(repository_cfg.read_bytes())


def test_accepted_runtime_cfg_loads_with_real_tender_processor_loader(
    tmp_path,
    monkeypatch,
):
    configure_runtime(monkeypatch, FakeBlob())
    result = runtime_config.prepare_runtime_config(tmp_path)

    assert result.active is True

    # Avoid constructing a real Gemini client while exercising the production
    # module's actual import-time load_config() path.
    monkeypatch.setattr(genai, "Client", lambda **_kwargs: object())
    module_name = "processing.tender_processor"
    previous_module = sys.modules.pop(module_name, None)
    processing_package = importlib.import_module("processing")
    previous_attribute = getattr(processing_package, "tender_processor", None)

    try:
        tender_processor = importlib.import_module(module_name)

        assert tender_processor.TRIAGE_MODEL == "gemini-triage"
        assert tender_processor.EXTRACTION_MODEL == "gemini-extraction"
        assert tender_processor.TRIAGE_TEMPERATURE == 0.1
        assert tender_processor.SUMMARY_TEMPERATURE == 0.2
        assert tender_processor.EXTRACTION_TEMPERATURE == 0.1
        assert tender_processor.TRIAGE_CHAR_LIMIT == 6000
    finally:
        sys.modules.pop(module_name, None)
        if previous_module is not None:
            sys.modules[module_name] = previous_module
        if previous_attribute is not None:
            processing_package.tender_processor = previous_attribute
        elif hasattr(processing_package, "tender_processor"):
            del processing_package.tender_processor
