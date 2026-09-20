-- Migration: add summary_headline column to tenders table
-- Run once against the live BigQuery dataset.
--
-- summary_headline is the AI-generated one-line headline produced by
-- tender_processor.py (TenderSummary.headline). It is kept in the
-- tenders table alongside description so the front-end can display a
-- short title without having to re-run the AI layer.

ALTER TABLE `tenderai-dev.TenderAI.tenders`
ADD COLUMN IF NOT EXISTS summary_headline STRING;
