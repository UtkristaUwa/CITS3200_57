"""
Tests for tender relevance determination (processing/relevance_determination.py)
and integration with tender_processor.py.
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from processing import relevance_determination as rd
from processing.relevance_determination import (
    RelevanceResult,
    build_user_prompt,
    calculate_recency_score,
    calculate_relevance_for_tender_record,
    calculate_tender_relevance,
    combine_relevance_and_recency,
    load_config,
    parse_publish_date,
)


class TestConfigAndPrompts:
    def test_loads_config_from_cfg(self):
        load_config()
        assert rd.RELEVANCE_MODEL in ("gemini-2.5-flash", "gemini-2.0-flash-lite")
        assert rd.RECENCY_WEIGHT == 0.10
        assert rd.RECENCY_DECAY_DAYS == 30
        assert "Social Ventures Australia (SVA)" in rd.RELEVANCE_SYSTEM_INSTRUCTION
        assert "Early Years" in rd.RELEVANCE_SYSTEM_INSTRUCTION
        assert "Return only the relevance score as an integer 1-100" in rd.RELEVANCE_SYSTEM_INSTRUCTION

    def test_build_user_prompt(self):
        prompt = build_user_prompt(
            title="Early Childhood Literacy Program",
            category="Request for Tender",
            description="Evaluating an education initiative for disadvantaged youth.",
        )
        assert "Title: Early Childhood Literacy Program" in prompt
        assert "Category: Request for Tender" in prompt
        assert "Description:\nEvaluating an education initiative for disadvantaged youth." in prompt

    def test_build_user_prompt_handles_missing_fields(self):
        prompt = build_user_prompt(title=None, category=None, description=None)
        assert "Title: Not stated" in prompt
        assert "Category: Not stated" in prompt
        assert "Description:\nNo description provided." in prompt


class TestDateParsingAndRecency:
    def test_parse_publish_date(self):
        assert parse_publish_date("2026-09-10") == date(2026, 9, 10)
        assert parse_publish_date("2026-09-10T14:00:00+10:00") == date(2026, 9, 10)
        assert parse_publish_date("2026-09-10 14:00:00") == date(2026, 9, 10)
        assert parse_publish_date(date(2026, 9, 10)) == date(2026, 9, 10)
        assert parse_publish_date(datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)) == date(2026, 9, 10)
        assert parse_publish_date("invalid-date") is None
        assert parse_publish_date(None) is None
        assert parse_publish_date("") is None

    def test_recency_score_today(self):
        ref = date(2026, 9, 25)
        # Published today -> 100.0
        assert calculate_recency_score("2026-09-25", decay_days=30, reference_date=ref) == 100.0

    def test_recency_score_future_advance_notice(self):
        ref = date(2026, 9, 25)
        # Advance notice published with future date -> 100.0
        assert calculate_recency_score("2026-09-26", decay_days=30, reference_date=ref) == 100.0

    def test_recency_score_halfway(self):
        ref = date(2026, 9, 25)
        pub = ref - timedelta(days=15)
        score = calculate_recency_score(pub, decay_days=30, reference_date=ref)
        assert pytest.approx(score, 0.1) == 50.0

    def test_recency_score_decayed(self):
        ref = date(2026, 9, 25)
        pub = ref - timedelta(days=30)
        assert calculate_recency_score(pub, decay_days=30, reference_date=ref) == 0.0

        pub_old = ref - timedelta(days=60)
        assert calculate_recency_score(pub_old, decay_days=30, reference_date=ref) == 0.0

    def test_recency_score_missing_date_is_neutral(self):
        assert calculate_recency_score(None) == 50.0
        assert calculate_recency_score("unparseable") == 50.0


class TestRelevanceAndRecencyCombination:
    def test_neutral_recency_preserves_base_score(self):
        # Neutral recency (50.0) leaves base score unaltered
        assert combine_relevance_and_recency(90, 50.0, recency_weight=0.10) == 90
        assert combine_relevance_and_recency(50, 50.0, recency_weight=0.10) == 50
        assert combine_relevance_and_recency(10, 50.0, recency_weight=0.10) == 10

    def test_fresh_boost(self):
        # Fresh tender (recency 100) receives up to +10% boost
        # 90 * 1.10 = 99
        assert combine_relevance_and_recency(90, 100.0, recency_weight=0.10) == 99

    def test_stale_penalty(self):
        # Stale tender (recency 0) receives up to -10% penalty
        # 90 * 0.90 = 81
        assert combine_relevance_and_recency(90, 0.0, recency_weight=0.10) == 81

    def test_clearly_irrelevant_stays_irrelevant(self):
        # Construction tender base=10 published today (recency=100)
        # 10 * 1.10 = 11 -> strictly in 1-14 range!
        combined = combine_relevance_and_recency(10, 100.0, recency_weight=0.10)
        assert 1 <= combined <= 14

        # Base=5 published today -> 5 * 1.10 = 5.5 -> 6
        assert combine_relevance_and_recency(5, 100.0, recency_weight=0.10) == 6

    def test_clamping_to_1_and_100(self):
        assert combine_relevance_and_recency(100, 100.0, recency_weight=0.10) == 100
        assert combine_relevance_and_recency(1, 0.0, recency_weight=0.10) == 1


class TestCalculateTenderRelevance:
    def test_empty_tender_defaults_to_neutral(self):
        # If both title and description are blank, returns baseline ~35 directly without API call
        score = calculate_tender_relevance(title="", description="")
        assert 30 <= score <= 40

    def test_relevance_with_mocked_gemini(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = RelevanceResult(relevance_score=92)
        mock_client.models.generate_content.return_value = mock_response

        score = calculate_tender_relevance(
            title="Early Childhood Intervention Program Evaluation",
            description="Evaluating outcomes for families and children facing disadvantage.",
            category="Request for Tender",
            publish_date=date.today(),
            client=mock_client,
        )

        assert mock_client.models.generate_content.called
        # Base 92 + fresh boost (today) -> 92 * 1.10 = 101 -> clamped to 100
        assert score == 100

    def test_calculate_relevance_for_tender_record(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = RelevanceResult(relevance_score=75)
        mock_client.models.generate_content.return_value = mock_response

        record = {
            "title": "Indigenous Community Youth Employment Initiative",
            "description": "Skills development and job placement for First Nations jobseekers.",
            "category": "Expression of Interest",
            "publish_date": None,
        }

        score = calculate_relevance_for_tender_record(record, client=mock_client)
        # Neutral recency (publish_date is None) -> exactly 75
        assert score == 75

    def test_fallback_when_gemini_returns_unstructured_json(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = None
        mock_response.text = '{"relevance_score": 85}'
        mock_client.models.generate_content.return_value = mock_response

        score = calculate_tender_relevance(
            title="Education Equity Strategy",
            description="School improvement consulting.",
            publish_date=None,
            client=mock_client,
        )
        assert score == 85


class TestProcessTenderIntegration:
    def test_process_tender_includes_relevance_score(self, tmp_path):
        from processing.tender_processor import process_tender, TenderSummary, TenderFields

        # Create a mock tender directory with a .txt document
        tender_file = tmp_path / "TENDER_001.txt"
        tender_file.write_text("Tender document text for early years evaluation.", encoding="utf-8")

        mock_summary = TenderSummary(
            headline="Dept seeks evaluator for early years initiative.",
            description="Detailed description for early years evaluation service.",
        )
        mock_fields = TenderFields(
            title="Early Years Evaluation",
            category="Request for Tender",
            publish_date="2026-09-20",
        )

        with patch("processing.tender_processor.summarise_tender", return_value=mock_summary), \
             patch("processing.tender_processor.extract_tender_fields", return_value=mock_fields), \
             patch("processing.tender_processor.calculate_tender_relevance", return_value=94) as mock_rel:

            record = process_tender(str(tmp_path))

            assert mock_rel.called
            assert "relevance_score" in record
            assert record["relevance_score"] == 94
            assert record["title"] == "Early Years Evaluation"

