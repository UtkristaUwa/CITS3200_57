"""Prepare a validated job-local tender processor configuration from GCS."""

from __future__ import annotations

import configparser
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from google.api_core import exceptions as google_exceptions
from google.auth import exceptions as google_auth_exceptions
from google.cloud import storage
from requests import exceptions as requests_exceptions


logger = logging.getLogger("RuntimeConfig")

RUNTIME_CONFIG_BUCKET = "RUNTIME_CONFIG_BUCKET"
RUNTIME_CONFIG_OBJECT = "RUNTIME_CONFIG_OBJECT"
TENDER_PROCESSOR_CONFIG = "TENDER_PROCESSOR_CONFIG"
_LOCAL_FILE_NAME = "runtime_tender_processor.cfg"
_REQUIRED_SECTIONS = {
    "models",
    "taxonomies",
    "system_prompts",
    "field_descriptions",
    "prompt_templates",
}


@dataclass(frozen=True)
class RuntimeConfigPreparation:
    active: bool
    path: str | None
    reason: str


class InvalidRuntimeConfig(ValueError):
    """The downloaded object is not compatible with tender_processor."""


def _validate_config(data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidRuntimeConfig("runtime configuration is not valid UTF-8") from exc

    try:
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.read_string(text)
    except configparser.Error as exc:
        raise InvalidRuntimeConfig("runtime configuration has invalid CFG syntax") from exc

    missing_sections = sorted(_REQUIRED_SECTIONS.difference(parser.sections()))
    if missing_sections:
        raise InvalidRuntimeConfig(
            "runtime configuration is missing required section(s): "
            + ", ".join(missing_sections)
        )

    for key in ("triage_model", "extraction_model"):
        if not parser.has_option("models", key):
            raise InvalidRuntimeConfig(f"runtime configuration is missing models.{key}")
        if not parser.get("models", key).strip():
            raise InvalidRuntimeConfig(f"runtime configuration has an empty models.{key}")

    numeric_options = (
        ("triage_temperature", parser.getfloat),
        ("summary_temperature", parser.getfloat),
        ("extraction_temperature", parser.getfloat),
        ("triage_char_limit", parser.getint),
    )
    for key, converter in numeric_options:
        try:
            # load_config() supplies defaults for absent numeric options, so
            # they remain optional here while present values use its exact
            # ConfigParser conversion semantics.
            converter("models", key, fallback=None)
        except (ValueError, configparser.Error) as exc:
            raise InvalidRuntimeConfig(
                f"runtime configuration has an invalid numeric models.{key}"
            ) from exc


def _fallback(reason: str) -> RuntimeConfigPreparation:
    logger.warning("Runtime configuration unavailable; using repository fallback: %s", reason)
    return RuntimeConfigPreparation(active=False, path=None, reason=reason)


def prepare_runtime_config(runtime_directory: str | os.PathLike[str]) -> RuntimeConfigPreparation:
    """Download and select the runtime CFG, or safely retain repository fallback.

    The caller owns ``runtime_directory`` and must keep it alive for the whole
    job. The GCS object is downloaded only once at startup; later changes apply
    to the next job invocation.
    """
    # Never leave a stale/custom path selected when this startup attempt falls
    # back. tender_processor will then use its repository-local default.
    os.environ.pop(TENDER_PROCESSOR_CONFIG, None)

    bucket_name = os.environ.get(RUNTIME_CONFIG_BUCKET, "").strip()
    object_name = os.environ.get(RUNTIME_CONFIG_OBJECT, "").strip()
    if not bucket_name or not object_name:
        return _fallback("RUNTIME_CONFIG_BUCKET and RUNTIME_CONFIG_OBJECT are not both configured")

    try:
        client = storage.Client()
        blob = client.bucket(bucket_name).blob(object_name)
        data = blob.download_as_bytes()
    except google_exceptions.NotFound:
        return _fallback("runtime configuration object was not found")
    except (
        google_exceptions.GoogleAPIError,
        google_auth_exceptions.GoogleAuthError,
        requests_exceptions.RequestException,
    ) as exc:
        return _fallback(f"runtime configuration download failed: {type(exc).__name__}")

    try:
        _validate_config(data)
    except InvalidRuntimeConfig as exc:
        return _fallback(str(exc))

    local_path = Path(runtime_directory) / _LOCAL_FILE_NAME
    try:
        local_path.write_bytes(data)
    except OSError as exc:
        return _fallback(f"could not write local runtime configuration: {exc}")

    os.environ[TENDER_PROCESSOR_CONFIG] = str(local_path)
    logger.info(
        "Runtime configuration downloaded and selected for this job; "
        "later GCS changes apply to the next job"
    )
    return RuntimeConfigPreparation(active=True, path=str(local_path), reason="runtime configuration active")
