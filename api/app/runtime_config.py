"""Read and safely update the shared runtime tender processor configuration."""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from functools import lru_cache

from google.api_core import exceptions as google_exceptions
from google.auth import exceptions as google_auth_exceptions
from google.cloud import storage
from requests import exceptions as requests_exceptions

from app.config import settings


_SECTION_RE = re.compile(r"^\[([^]]+)]\s*(?:[#;].*)?(?:\r?\n)?$")
_MODEL_KEY_RE = re.compile(
    r"^(?P<key>triage_model|extraction_model)(?P<separator>\s*=\s*)"
    r"(?P<value>[^\r\n]*?)(?P<ending>\r?\n)?$",
    re.IGNORECASE,
)
_MODEL_KEYS = {"triage_model", "extraction_model"}


class RuntimeConfigError(Exception):
    """Base exception for errors that are safe to translate at the API boundary."""


class RuntimeConfigUnavailable(RuntimeConfigError):
    """The runtime configuration cannot currently be read or persisted."""


class RuntimeConfigMalformed(RuntimeConfigError):
    """The stored object is not a valid, unambiguous processor configuration."""


class RuntimeConfigConflict(RuntimeConfigError):
    """The stored object changed after the caller read it."""


@dataclass(frozen=True)
class RuntimeModelConfig:
    triage_model: str
    extraction_model: str
    generation: str


@dataclass(frozen=True)
class _StoredConfig:
    text: str
    models: dict[str, str]
    generation: str


@lru_cache
def get_storage_client() -> storage.Client:
    return storage.Client(project=settings.google_cloud_project)


def _runtime_location() -> tuple[str, str]:
    bucket = settings.runtime_config_bucket.strip()
    object_name = settings.runtime_config_object.strip()
    if not bucket or not object_name:
        raise RuntimeConfigUnavailable("runtime model configuration is not configured")
    return bucket, object_name


def _parse_models(text: str) -> dict[str, str]:
    """Validate the CFG and find exactly one approved key inside one [models]."""
    try:
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.read_string(text)
    except configparser.Error as exc:
        raise RuntimeConfigMalformed("runtime configuration is malformed") from exc

    if not parser.has_section("models"):
        raise RuntimeConfigMalformed("runtime configuration has no [models] section")

    lines = text.splitlines(keepends=True)
    in_models = False
    models_sections = 0
    matches: dict[str, list[str]] = {key: [] for key in _MODEL_KEYS}

    for line in lines:
        section_match = _SECTION_RE.match(line)
        if section_match:
            in_models = section_match.group(1).strip().lower() == "models"
            if in_models:
                models_sections += 1
            continue

        if not in_models:
            continue
        key_match = _MODEL_KEY_RE.match(line)
        if key_match:
            key = key_match.group("key").lower()
            matches[key].append(key_match.group("value").strip())

    if models_sections != 1 or any(len(matches[key]) != 1 for key in _MODEL_KEYS):
        raise RuntimeConfigMalformed(
            "runtime configuration must contain exactly one triage_model and "
            "one extraction_model in a single [models] section"
        )

    models = {key: values[0] for key, values in matches.items()}
    if any(not value for value in models.values()):
        raise RuntimeConfigMalformed("runtime configuration contains an empty model identifier")

    # The targeted line scan and ConfigParser must agree. In particular, an
    # indented line can look like an assignment to a regex while ConfigParser
    # treats it as a continuation of the preceding option; such a line is not
    # a real top-level model key and must never be patched.
    for key, scanned_value in models.items():
        if not parser.has_option("models", key):
            raise RuntimeConfigMalformed(f"runtime configuration has no top-level {key}")
        if parser.get("models", key).strip() != scanned_value:
            raise RuntimeConfigMalformed(f"runtime configuration has an ambiguous {key}")
    return models


def _download_current(*, precondition_is_conflict: bool = False) -> tuple[object, _StoredConfig]:
    bucket_name, object_name = _runtime_location()

    try:
        blob = get_storage_client().bucket(bucket_name).blob(object_name)
        blob.reload()
        generation = blob.generation
        if generation is None:
            raise RuntimeConfigUnavailable("runtime configuration has no generation")
        raw = blob.download_as_bytes(if_generation_match=generation)
        text = raw.decode("utf-8")
    except google_exceptions.NotFound as exc:
        raise RuntimeConfigUnavailable("runtime model configuration is not initialized") from exc
    except google_exceptions.PreconditionFailed as exc:
        if precondition_is_conflict:
            raise RuntimeConfigConflict(
                "runtime model configuration has changed; reload and try again"
            ) from exc
        raise RuntimeConfigUnavailable("runtime model configuration changed while being read") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeConfigMalformed("runtime configuration is not valid UTF-8") from exc
    except RuntimeConfigError:
        raise
    except (
        google_exceptions.GoogleAPIError,
        google_auth_exceptions.GoogleAuthError,
        requests_exceptions.RequestException,
    ) as exc:
        raise RuntimeConfigUnavailable("runtime model configuration is unavailable") from exc

    models = _parse_models(text)
    return blob, _StoredConfig(text=text, models=models, generation=str(generation))


def get_model_config() -> RuntimeModelConfig:
    _, stored = _download_current()
    return RuntimeModelConfig(
        triage_model=stored.models["triage_model"],
        extraction_model=stored.models["extraction_model"],
        generation=stored.generation,
    )


def _replace_models(text: str, updates: dict[str, str]) -> str:
    lines = text.splitlines(keepends=True)
    in_models = False
    replacement_counts = {key: 0 for key in updates}
    updated_lines: list[str] = []

    for line in lines:
        section_match = _SECTION_RE.match(line)
        if section_match:
            in_models = section_match.group(1).strip().lower() == "models"
            updated_lines.append(line)
            continue

        key_match = _MODEL_KEY_RE.match(line) if in_models else None
        if key_match and key_match.group("key").lower() in updates:
            key = key_match.group("key").lower()
            replacement_counts[key] += 1
            updated_lines.append(
                f'{key_match.group("key")}'
                f'{key_match.group("separator")}{updates[key]}'
                f'{key_match.group("ending") or ""}'
            )
        else:
            updated_lines.append(line)

    if any(count != 1 for count in replacement_counts.values()):
        raise RuntimeConfigMalformed("approved model keys could not be updated unambiguously")
    return "".join(updated_lines)


def update_model_config(
    *,
    expected_generation: str,
    triage_model: str | None = None,
    extraction_model: str | None = None,
) -> RuntimeModelConfig:
    blob, stored = _download_current(precondition_is_conflict=True)
    if stored.generation != expected_generation:
        raise RuntimeConfigConflict("runtime model configuration has changed; reload and try again")

    updates = {
        key: value
        for key, value in {
            "triage_model": triage_model,
            "extraction_model": extraction_model,
        }.items()
        if value is not None
    }
    updated_text = _replace_models(stored.text, updates)
    updated_models = _parse_models(updated_text)

    if any(updated_models[key] != value for key, value in updates.items()):
        raise RuntimeConfigMalformed("updated runtime configuration failed validation")

    try:
        blob.upload_from_string(
            updated_text.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
            if_generation_match=int(expected_generation),
        )
    except google_exceptions.PreconditionFailed as exc:
        raise RuntimeConfigConflict(
            "runtime model configuration has changed; reload and try again"
        ) from exc
    except (
        google_exceptions.GoogleAPIError,
        google_auth_exceptions.GoogleAuthError,
        requests_exceptions.RequestException,
    ) as exc:
        raise RuntimeConfigUnavailable("runtime model configuration could not be saved") from exc

    new_generation = blob.generation
    if new_generation is None:
        raise RuntimeConfigUnavailable("saved runtime configuration has no generation")
    return RuntimeModelConfig(
        triage_model=updated_models["triage_model"],
        extraction_model=updated_models["extraction_model"],
        generation=str(new_generation),
    )
