"""
TenderAI Relevance Determination Engine
Scores Australian government tenders for relevance to Social Ventures Australia (SVA)
on an integer scale from 1 to 100.

Relevance is determined primarily by the tender's title, description, and category,
with a calibrated recency weighting factor (more recent tenders receive a light boost,
older tenders receive a light decay, while strictly preserving tier classification).

Configuration is loaded from tender_processor.cfg.
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Any, Optional, Union

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Fallback defaults if config file is absent or incomplete
DEFAULT_RELEVANCE_PROMPT = """You are scoring Australian government tenders/procurement opportunities for relevance to Social Ventures Australia (SVA), a social impact organisation.

SVA's core areas:
- Early Years (early childhood development, out-of-home care, family support)
- Education (school improvement, teacher development, student outcomes, education equity)
- First Nations (Indigenous-led programs, closing the gap, reconciliation, First Nations economic development)
- Employment (disadvantaged jobseekers, disability employment, youth employment, workforce participation)
- Cross-cutting: social impact consulting, program evaluation, impact investing, social policy/advocacy, community services, disadvantage/inequality, not-for-profit capacity building, data/evaluation for social outcomes

Score the tender's relevance to SVA from 1-100:
- 90-100: Directly matches SVA's expertise (e.g. evaluation of an early years program, First Nations employment initiative, education equity consulting)
- 70-89: Strongly related sector (social services, disadvantage, community/Indigenous programs) even if not an exact service match
- 40-69: Loosely related (general govt consulting/advisory/research, broader social policy, data analysis for public sector) — plausible but not core
- 15-39: Unrelated sector but still a professional/advisory services tender SVA could theoretically deliver (IT systems, HR, generic project management)
- 1-14: Clearly irrelevant (construction, defence, hardware procurement, unrelated physical/technical works)

Base the score only on the title, description and category provided — don't assume info that isn't there. If the description is too thin to judge confidently, default to around 30-40 rather than guessing high.

Return only the relevance score as an integer 1-100."""

DEFAULT_USER_PROMPT = """Title: {title}
Category: {category}
Description:
{description}"""

_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tender_processor.cfg")
_CONFIG_PATH: str = _DEFAULT_CONFIG_PATH
_LAST_CONFIG_MTIME: Optional[float] = None

# Global configuration variables populated by load_config()
RELEVANCE_MODEL: str = "gemini-2.5-flash"
RELEVANCE_TEMPERATURE: float = 0.1
RELEVANCE_SYSTEM_INSTRUCTION: str = DEFAULT_RELEVANCE_PROMPT
RECENCY_WEIGHT: float = 0.10
RECENCY_DECAY_DAYS: int = 30
USER_PROMPT_TEMPLATE: str = DEFAULT_USER_PROMPT


def is_retryable_error(exc: BaseException) -> bool:
    """Retry on Vertex/Gemini server errors (5xx) and rate limits (429 RESOURCE_EXHAUSTED)."""
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.APIError) and exc.code == 429:
        return True
    return False


class RelevanceResult(BaseModel):
    """Structured output schema for the AI relevance score."""
    relevance_score: int = Field(
        ...,
        description="Relevance score to SVA as an integer from 1 to 100",
        ge=1,
        le=100,
    )


def _parse_lenient_cfg(filepath: str) -> configparser.ConfigParser:
    """
    Parses a .cfg file leniently so that multiline prompts and blank lines within
    prompt blocks do not cause ConfigParser errors.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    processed_lines = []
    in_value = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_value:
                processed_lines.append("    \n")
            else:
                processed_lines.append("\n")
        elif stripped.startswith(("[", ";", "#")):
            in_value = False
            processed_lines.append(line)
        elif "=" in line and not line.startswith((" ", "\t")):
            in_value = True
            processed_lines.append(line)
        else:
            if in_value and not line.startswith((" ", "\t")):
                processed_lines.append("    " + line)
            else:
                processed_lines.append(line)

    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string("".join(processed_lines))
    return parser


def load_config(config_path: Optional[str] = None):
    """
    Loads relevance models, hyperparameters, system instructions, and recency settings
    from the external tender_processor.cfg file.
    """
    global _CONFIG_PATH, _LAST_CONFIG_MTIME
    global RELEVANCE_MODEL, RELEVANCE_TEMPERATURE, RELEVANCE_SYSTEM_INSTRUCTION
    global RECENCY_WEIGHT, RECENCY_DECAY_DAYS, USER_PROMPT_TEMPLATE

    resolved_path = config_path or os.getenv("TENDER_PROCESSOR_CONFIG") or _DEFAULT_CONFIG_PATH

    if not os.path.isfile(resolved_path):
        alt_path = os.path.join(os.getcwd(), "tender_processor.cfg")
        if os.path.isfile(alt_path):
            resolved_path = alt_path
        else:
            return

    cfg = _parse_lenient_cfg(resolved_path)

    if cfg.has_section("models"):
        RELEVANCE_MODEL = cfg.get("models", "relevance_model", fallback=cfg.get("models", "extraction_model", fallback=RELEVANCE_MODEL))
        RELEVANCE_TEMPERATURE = cfg.getfloat("models", "relevance_temperature", fallback=RELEVANCE_TEMPERATURE)

    if cfg.has_section("relevance"):
        RECENCY_WEIGHT = cfg.getfloat("relevance", "recency_weight", fallback=RECENCY_WEIGHT)
        RECENCY_DECAY_DAYS = cfg.getint("relevance", "recency_decay_days", fallback=RECENCY_DECAY_DAYS)

    if cfg.has_section("system_prompts"):
        if cfg.has_option("system_prompts", "relevance"):
            RELEVANCE_SYSTEM_INSTRUCTION = cfg.get("system_prompts", "relevance").strip()

    if cfg.has_section("prompt_templates"):
        if cfg.has_option("prompt_templates", "relevance_user_prompt"):
            USER_PROMPT_TEMPLATE = cfg.get("prompt_templates", "relevance_user_prompt").strip()

    _CONFIG_PATH = resolved_path
    try:
        _LAST_CONFIG_MTIME = os.path.getmtime(resolved_path)
    except OSError:
        _LAST_CONFIG_MTIME = None


def _check_auto_reload():
    """Checks if the configuration file on disk has been updated, and reloads if necessary."""
    global _CONFIG_PATH, _LAST_CONFIG_MTIME
    if _CONFIG_PATH and os.path.isfile(_CONFIG_PATH):
        try:
            mtime = os.path.getmtime(_CONFIG_PATH)
            if _LAST_CONFIG_MTIME is not None and mtime > _LAST_CONFIG_MTIME:
                load_config(_CONFIG_PATH)
        except OSError:
            pass


# Initial load
load_config()


def _get_default_client() -> genai.Client:
    """Build a default Gemini Client using Vertex AI or GEMINI_API_KEY."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        return genai.Client(api_key=api_key)
    return genai.Client(
        vertexai=True,
        project="tenderai-dev",
        location="australia-southeast1",
    )


def parse_publish_date(date_val: Union[str, date, datetime, None]) -> Optional[date]:
    """Parse various date formats into a date object."""
    if date_val is None:
        return None
    if isinstance(date_val, datetime):
        return date_val.date()
    if isinstance(date_val, date):
        return date_val
    if isinstance(date_val, str):
        cleaned = date_val.strip()
        if not cleaned:
            return None
        if "T" in cleaned:
            cleaned = cleaned.split("T")[0]
        elif " " in cleaned:
            cleaned = cleaned.split(" ")[0]
        try:
            return datetime.strptime(cleaned, "%Y-%m-%d").date()
        except ValueError:
            pass
        for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(cleaned, fmt).date()
            except ValueError:
                pass
    return None


def calculate_recency_score(
    publish_date: Union[str, date, datetime, None],
    decay_days: Optional[int] = None,
    reference_date: Optional[date] = None,
) -> float:
    """
    Calculates a recency score from 0.0 to 100.0 based on elapsed days since publish_date.
    - If publish_date is None or unparseable: returns 50.0 (neutral baseline).
    - If published today (or in future): returns 100.0.
    - If published >= decay_days ago: returns 0.0.
    - Decays linearly between 100.0 and 0.0 over decay_days.
    """
    if decay_days is None:
        decay_days = RECENCY_DECAY_DAYS

    parsed_date = parse_publish_date(publish_date)
    if parsed_date is None:
        return 50.0

    if reference_date is None:
        reference_date = datetime.now(timezone.utc).date()

    age_days = (reference_date - parsed_date).days
    if age_days <= 0:
        return 100.0
    if age_days >= decay_days:
        return 0.0

    return 100.0 * (1.0 - (age_days / float(decay_days)))


def combine_relevance_and_recency(
    base_score: int,
    recency_score: float,
    recency_weight: Optional[float] = None,
) -> int:
    """
    Blends the AI content relevance score with the recency score.
    Uses proportional adjustment around the neutral baseline (50.0) so that:
    - Highly relevant tenders receive a light boost if fresh, light penalty if stale.
    - Clearly irrelevant tenders (e.g. score < 15) are NOT falsely promoted.
    - Neutral recency (50.0) leaves base score unaffected.
    - Result is clamped to an integer in [1, 100].
    """
    if recency_weight is None:
        recency_weight = RECENCY_WEIGHT

    base = max(1, min(100, int(base_score)))
    recency_delta = (recency_score - 50.0) / 50.0
    adjusted = base * (1.0 + recency_weight * recency_delta)
    final_score = int(round(adjusted))
    return max(1, min(100, final_score))


def build_user_prompt(
    title: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
) -> str:
    """Constructs the prompt sent to Gemini based on tender fields."""
    _check_auto_reload()
    safe_title = (title or "").strip() or "Not stated"
    safe_category = (category or "").strip() or "Not stated"
    safe_description = (description or "").strip() or "No description provided."

    return USER_PROMPT_TEMPLATE.format(
        title=safe_title,
        category=safe_category,
        description=safe_description,
    )


@retry(
    retry=retry_if_exception(is_retryable_error),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _call_gemini_relevance(prompt: str, client: genai.Client) -> int:
    """Calls Gemini to score tender relevance according to SVA criteria."""
    _check_auto_reload()
    response = client.models.generate_content(
        model=RELEVANCE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=RELEVANCE_SYSTEM_INSTRUCTION,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            response_mime_type="application/json",
            response_schema=RelevanceResult,
            temperature=RELEVANCE_TEMPERATURE,
        ),
    )

    if response.parsed and isinstance(response.parsed, RelevanceResult):
        return response.parsed.relevance_score

    # Fallback text parsing if parsed schema object is unavailable
    text = response.text or ""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "relevance_score" in data:
            return int(data["relevance_score"])
    except Exception:
        pass

    match = re.search(r"\b([1-9][0-9]?|100)\b", text)
    if match:
        return int(match.group(1))

    # Default per prompt: if description is too thin to judge, default around 30-40
    return 35


def calculate_tender_relevance(
    title: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    publish_date: Optional[Union[str, date, datetime]] = None,
    client: Optional[genai.Client] = None,
    **kwargs: Any,
) -> int:
    """
    Main entry point for determining tender relevance:
    1. Evaluates content relevance (1-100) using Gemini with the SVA prompt.
    2. Computes recency score based on publish_date.
    3. Combines content relevance and recency with a light weighting.
    4. Returns an integer in [1, 100].
    """
    _check_auto_reload()

    # If title and description are completely absent/empty, return default baseline directly
    if not (title or "").strip() and not (description or "").strip():
        base_score = 35
    else:
        active_client = client or _get_default_client()
        user_prompt = build_user_prompt(title=title, description=description, category=category)
        base_score = _call_gemini_relevance(user_prompt, active_client)

    base_score = max(1, min(100, base_score))
    recency_score = calculate_recency_score(publish_date)
    final_score = combine_relevance_and_recency(base_score, recency_score)

    return final_score


def calculate_relevance_for_tender_record(
    record: dict,
    client: Optional[genai.Client] = None,
) -> int:
    """Convenience helper to score relevance directly from a tender dictionary."""
    return calculate_tender_relevance(
        title=record.get("title"),
        description=record.get("description"),
        category=record.get("category"),
        publish_date=record.get("publish_date"),
        client=client,
    )
