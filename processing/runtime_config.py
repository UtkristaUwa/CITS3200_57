"""Prepare a validated job-local tender processor configuration from GCS."""

from __future__ import annotations

import configparser
import logging
import math
import os
import re
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
    "relevance_prompts",
    "relevance_scoring",
    "taxonomies",
    "system_prompts",
    "field_descriptions",
    "prompt_templates",
}
_RELEVANCE_PROMPT_KEYS = (
    "classification_thoughts",
    "focus_areas",
    "work_types",
    "out_of_scope",
)
_RELEVANCE_FLOAT_OPTIONS = (
    ("relevance_scoring", "focus_area_weight"),
    ("relevance_scoring", "work_type_weight"),
    ("relevance_scoring", "recency_weight"),
    ("models", "relevance_temperature"),
)
_RELEVANCE_INT_KEYS = (
    "recency_horizon_days",
    "out_of_scope_fit_cap",
    "max_focus_areas",
    "max_work_types",
)
_TAG_DEFINITION = re.compile(r"^([a-z][a-z0-9_]*)\s*\|\s*\S")


@dataclass(frozen=True)
class RuntimeConfigPreparation:
    active: bool
    path: str | None
    reason: str


class InvalidRuntimeConfig(ValueError):
    """The downloaded object is not compatible with tender_processor."""


def _taxonomy_tag_ids(value: str) -> list[str]:
    return [
        match.group(1)
        for line in value.splitlines()
        if (match := _TAG_DEFINITION.match(line.strip()))
    ]


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

    relevance_prompts: dict[str, str] = {}
    for key in _RELEVANCE_PROMPT_KEYS:
        if not parser.has_option("relevance_prompts", key):
            raise InvalidRuntimeConfig(
                f"runtime configuration is missing relevance_prompts.{key}"
            )
        value = parser.get("relevance_prompts", key).strip()
        if not value:
            raise InvalidRuntimeConfig(
                f"runtime configuration has an empty relevance_prompts.{key}"
            )
        relevance_prompts[key] = value

    if not parser.has_option("models", "relevance_model"):
        raise InvalidRuntimeConfig("runtime configuration is missing models.relevance_model")
    if not parser.get("models", "relevance_model").strip():
        raise InvalidRuntimeConfig("runtime configuration has an empty models.relevance_model")

    relevance_numbers: dict[str, float | int] = {}
    for section, key in _RELEVANCE_FLOAT_OPTIONS:
        if not parser.has_option(section, key):
            raise InvalidRuntimeConfig(f"runtime configuration is missing {section}.{key}")
        try:
            value = parser.getfloat(section, key)
        except (ValueError, configparser.Error) as exc:
            raise InvalidRuntimeConfig(
                f"runtime configuration has an invalid numeric {section}.{key}"
            ) from exc
        if not math.isfinite(value):
            raise InvalidRuntimeConfig(
                f"runtime configuration has an invalid numeric {section}.{key}"
            )
        relevance_numbers[key] = value

    for key in _RELEVANCE_INT_KEYS:
        if not parser.has_option("relevance_scoring", key):
            raise InvalidRuntimeConfig(
                f"runtime configuration is missing relevance_scoring.{key}"
            )
        try:
            relevance_numbers[key] = parser.getint("relevance_scoring", key)
        except (ValueError, configparser.Error) as exc:
            raise InvalidRuntimeConfig(
                f"runtime configuration has an invalid numeric relevance_scoring.{key}"
            ) from exc

    focus_weight = float(relevance_numbers["focus_area_weight"])
    work_weight = float(relevance_numbers["work_type_weight"])
    if not 0 <= focus_weight <= 1 or not 0 <= work_weight <= 1:
        raise InvalidRuntimeConfig("relevance scoring weights must be between 0 and 1")
    if not math.isclose(focus_weight + work_weight, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise InvalidRuntimeConfig("relevance scoring weights must sum to 1")
    if float(relevance_numbers["recency_weight"]) < 0:
        raise InvalidRuntimeConfig("relevance recency weight must not be negative")
    if int(relevance_numbers["recency_horizon_days"]) <= 0:
        raise InvalidRuntimeConfig("relevance recency horizon must be greater than zero")
    if not 0 <= int(relevance_numbers["out_of_scope_fit_cap"]) <= 100:
        raise InvalidRuntimeConfig("relevance out-of-scope cap must be between 0 and 100")
    if (
        int(relevance_numbers["max_focus_areas"]) <= 0
        or int(relevance_numbers["max_work_types"]) <= 0
    ):
        raise InvalidRuntimeConfig("relevance maximum tag counts must be greater than zero")
    if float(relevance_numbers["relevance_temperature"]) < 0:
        raise InvalidRuntimeConfig("relevance temperature must not be negative")

    for key in ("focus_areas", "work_types"):
        tag_ids = _taxonomy_tag_ids(relevance_prompts[key])
        if not tag_ids:
            raise InvalidRuntimeConfig(
                f"runtime configuration has no valid tag definitions in relevance_prompts.{key}"
            )
        if len(tag_ids) != len(set(tag_ids)):
            raise InvalidRuntimeConfig(
                f"runtime configuration has duplicate tag IDs in relevance_prompts.{key}"
            )


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
