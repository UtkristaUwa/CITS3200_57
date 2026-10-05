"""Read and safely update the shared runtime tender processor configuration."""

from __future__ import annotations

import configparser
import math
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
_RELEVANCE_PROMPT_KEYS = (
    "classification_thoughts",
    "focus_areas",
    "work_types",
    "out_of_scope",
)
_RELEVANCE_SCORING_KEYS = (
    "focus_area_weight",
    "work_type_weight",
    "recency_weight",
    "recency_horizon_days",
    "out_of_scope_fit_cap",
    "max_focus_areas",
    "max_work_types",
)
_RELEVANCE_MODEL_KEYS = ("relevance_model", "relevance_temperature")
_OPTION_RE = re.compile(
    r"^(?P<key>[A-Za-z][A-Za-z0-9_]*)(?P<separator>\s*=\s*)"
    r"(?P<value>[^\r\n]*?)(?P<ending>\r?\n)?$"
)
_TAG_DEFINITION = re.compile(r"^([a-z][a-z0-9_]*)\s*\|\s*\S")


class RuntimeConfigError(Exception):
    """Base exception for errors that are safe to translate at the API boundary."""


class RuntimeConfigUnavailable(RuntimeConfigError):
    """The runtime configuration cannot currently be read or persisted."""


class RuntimeConfigMalformed(RuntimeConfigError):
    """The stored object is not a valid, unambiguous processor configuration."""


class RuntimeConfigConflict(RuntimeConfigError):
    """The stored object changed after the caller read it."""


class RuntimeConfigInvalidUpdate(RuntimeConfigError):
    """A requested update would make the runtime configuration invalid."""


@dataclass(frozen=True)
class RuntimeModelConfig:
    triage_model: str
    extraction_model: str
    generation: str


@dataclass(frozen=True)
class RuntimeRelevanceConfig:
    classification_thoughts: str
    focus_areas: str
    work_types: str
    out_of_scope: str
    focus_area_weight: float
    work_type_weight: float
    recency_weight: float
    recency_horizon_days: int
    out_of_scope_fit_cap: int
    max_focus_areas: int
    max_work_types: int
    relevance_model: str
    relevance_temperature: float
    generation: str


@dataclass(frozen=True)
class _StoredConfig:
    text: str
    models: dict[str, str]
    generation: str


def _taxonomy_tag_ids(value: str) -> list[str]:
    return [
        match.group(1)
        for line in value.splitlines()
        if (match := _TAG_DEFINITION.match(line.strip()))
    ]


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


def _parse_relevance(text: str) -> dict[str, str | float | int]:
    try:
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.read_string(text)
    except configparser.Error as exc:
        raise RuntimeConfigMalformed("runtime configuration is malformed") from exc

    for section in ("models", "relevance_prompts", "relevance_scoring"):
        if not parser.has_section(section):
            raise RuntimeConfigMalformed(f"runtime configuration has no [{section}] section")

    values: dict[str, str | float | int] = {}
    for key in _RELEVANCE_PROMPT_KEYS:
        if not parser.has_option("relevance_prompts", key):
            raise RuntimeConfigMalformed(f"runtime configuration has no relevance_prompts.{key}")
        value = parser.get("relevance_prompts", key).strip()
        if not value:
            raise RuntimeConfigMalformed(f"runtime configuration has an empty relevance_prompts.{key}")
        values[key] = value

    for key in ("focus_areas", "work_types"):
        tag_ids = _taxonomy_tag_ids(str(values[key]))
        if not tag_ids:
            raise RuntimeConfigMalformed(
                f"runtime configuration has no valid tag definitions in relevance_prompts.{key}"
            )
        if len(tag_ids) != len(set(tag_ids)):
            raise RuntimeConfigMalformed(
                f"runtime configuration has duplicate tag IDs in relevance_prompts.{key}"
            )

    if not parser.has_option("models", "relevance_model"):
        raise RuntimeConfigMalformed("runtime configuration has no models.relevance_model")
    relevance_model = parser.get("models", "relevance_model").strip()
    if not relevance_model:
        raise RuntimeConfigMalformed("runtime configuration has an empty models.relevance_model")
    values["relevance_model"] = relevance_model

    float_fields = (
        ("relevance_scoring", "focus_area_weight"),
        ("relevance_scoring", "work_type_weight"),
        ("relevance_scoring", "recency_weight"),
        ("models", "relevance_temperature"),
    )
    for section, key in float_fields:
        if not parser.has_option(section, key):
            raise RuntimeConfigMalformed(f"runtime configuration has no {section}.{key}")
        try:
            value = parser.getfloat(section, key)
        except (ValueError, configparser.Error) as exc:
            raise RuntimeConfigMalformed(
                f"runtime configuration has an invalid {section}.{key}"
            ) from exc
        if not math.isfinite(value):
            raise RuntimeConfigMalformed(f"runtime configuration has an invalid {section}.{key}")
        values[key] = value

    int_fields = (
        "recency_horizon_days",
        "out_of_scope_fit_cap",
        "max_focus_areas",
        "max_work_types",
    )
    for key in int_fields:
        if not parser.has_option("relevance_scoring", key):
            raise RuntimeConfigMalformed(f"runtime configuration has no relevance_scoring.{key}")
        try:
            values[key] = parser.getint("relevance_scoring", key)
        except (ValueError, configparser.Error) as exc:
            raise RuntimeConfigMalformed(
                f"runtime configuration has an invalid relevance_scoring.{key}"
            ) from exc

    focus_weight = float(values["focus_area_weight"])
    work_weight = float(values["work_type_weight"])
    if not 0 <= focus_weight <= 1 or not 0 <= work_weight <= 1:
        raise RuntimeConfigMalformed("relevance scoring weights must be between 0 and 1")
    if not math.isclose(focus_weight + work_weight, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeConfigMalformed("relevance scoring weights must sum to 1")
    if float(values["recency_weight"]) < 0:
        raise RuntimeConfigMalformed("relevance recency weight must not be negative")
    if int(values["recency_horizon_days"]) <= 0:
        raise RuntimeConfigMalformed("relevance recency horizon must be greater than zero")
    if not 0 <= int(values["out_of_scope_fit_cap"]) <= 100:
        raise RuntimeConfigMalformed("relevance out-of-scope cap must be between 0 and 100")
    if int(values["max_focus_areas"]) <= 0 or int(values["max_work_types"]) <= 0:
        raise RuntimeConfigMalformed("relevance maximum tag counts must be greater than zero")
    if float(values["relevance_temperature"]) < 0:
        raise RuntimeConfigMalformed("relevance temperature must not be negative")

    return values


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


def _relevance_config(values: dict[str, str | float | int], generation: str) -> RuntimeRelevanceConfig:
    return RuntimeRelevanceConfig(
        classification_thoughts=str(values["classification_thoughts"]),
        focus_areas=str(values["focus_areas"]),
        work_types=str(values["work_types"]),
        out_of_scope=str(values["out_of_scope"]),
        focus_area_weight=float(values["focus_area_weight"]),
        work_type_weight=float(values["work_type_weight"]),
        recency_weight=float(values["recency_weight"]),
        recency_horizon_days=int(values["recency_horizon_days"]),
        out_of_scope_fit_cap=int(values["out_of_scope_fit_cap"]),
        max_focus_areas=int(values["max_focus_areas"]),
        max_work_types=int(values["max_work_types"]),
        relevance_model=str(values["relevance_model"]),
        relevance_temperature=float(values["relevance_temperature"]),
        generation=generation,
    )


def get_relevance_config() -> RuntimeRelevanceConfig:
    _, stored = _download_current()
    return _relevance_config(_parse_relevance(stored.text), stored.generation)


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


def _replace_relevance_values(text: str, updates: dict[str, str]) -> str:
    sections = {
        **{key: "relevance_prompts" for key in _RELEVANCE_PROMPT_KEYS},
        **{key: "relevance_scoring" for key in _RELEVANCE_SCORING_KEYS},
        **{key: "models" for key in _RELEVANCE_MODEL_KEYS},
    }
    lines = text.splitlines(keepends=True)
    replacement_counts = {key: 0 for key in updates}
    updated_lines: list[str] = []
    current_section = ""
    index = 0

    while index < len(lines):
        line = lines[index]
        section_match = _SECTION_RE.match(line)
        if section_match:
            current_section = section_match.group(1).strip().lower()
            updated_lines.append(line)
            index += 1
            continue

        option_match = _OPTION_RE.match(line)
        key = option_match.group("key").lower() if option_match else ""
        if option_match and key in updates and sections[key] == current_section:
            replacement_counts[key] += 1
            end = index + 1
            while end < len(lines) and (not lines[end].strip() or lines[end][0].isspace()):
                end += 1
            span_end = end

            preserved_comments = [
                span_line
                for span_line in lines[index + 1:end]
                if span_line.lstrip().startswith(("#", ";"))
            ]
            trailing_blanks: list[str] = []
            while end > index + 1 and not lines[end - 1].strip():
                trailing_blanks.insert(0, lines[end - 1])
                end -= 1

            original_ending = option_match.group("ending")
            line_ending = original_ending or ("\r\n" if "\r\n" in text else "\n")
            value_lines = updates[key].splitlines() or [""]
            first_ending = line_ending if original_ending is not None or len(value_lines) > 1 else ""
            updated_lines.append(
                f'{option_match.group("key")}{option_match.group("separator")}'
                f"{value_lines[0]}{first_ending}"
            )
            for value_index, value in enumerate(value_lines[1:], start=1):
                ending = (
                    line_ending
                    if original_ending is not None or value_index < len(value_lines) - 1
                    else ""
                )
                updated_lines.append(f"    {value}{ending}")
            updated_lines.extend(preserved_comments)
            updated_lines.extend(trailing_blanks)
            index = span_end
            continue

        updated_lines.append(line)
        index += 1

    if any(count != 1 for count in replacement_counts.values()):
        raise RuntimeConfigMalformed("approved relevance keys could not be updated unambiguously")
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


def update_relevance_config(
    *,
    expected_generation: str,
    classification_thoughts: str | None = None,
    focus_areas: str | None = None,
    work_types: str | None = None,
    out_of_scope: str | None = None,
    focus_area_weight: float | None = None,
    work_type_weight: float | None = None,
    recency_weight: float | None = None,
    recency_horizon_days: int | None = None,
    out_of_scope_fit_cap: int | None = None,
    max_focus_areas: int | None = None,
    max_work_types: int | None = None,
    relevance_model: str | None = None,
    relevance_temperature: float | None = None,
) -> RuntimeRelevanceConfig:
    blob, stored = _download_current(precondition_is_conflict=True)
    if stored.generation != expected_generation:
        raise RuntimeConfigConflict("runtime model configuration has changed; reload and try again")
    _parse_relevance(stored.text)

    raw_updates = {
        "classification_thoughts": classification_thoughts,
        "focus_areas": focus_areas,
        "work_types": work_types,
        "out_of_scope": out_of_scope,
        "focus_area_weight": focus_area_weight,
        "work_type_weight": work_type_weight,
        "recency_weight": recency_weight,
        "recency_horizon_days": recency_horizon_days,
        "out_of_scope_fit_cap": out_of_scope_fit_cap,
        "max_focus_areas": max_focus_areas,
        "max_work_types": max_work_types,
        "relevance_model": relevance_model,
        "relevance_temperature": relevance_temperature,
    }
    updates = {key: str(value) for key, value in raw_updates.items() if value is not None}
    updated_text = _replace_relevance_values(stored.text, updates)
    try:
        updated_values = _parse_relevance(updated_text)
    except RuntimeConfigMalformed as exc:
        raise RuntimeConfigInvalidUpdate(str(exc)) from exc

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
    return _relevance_config(updated_values, str(new_generation))
