-- Migration: add relevance_score column to tenders table
-- Run once against the live BigQuery dataset.
--
-- relevance_score is the AI-generated relevance score (1-100) produced by
-- relevance_determination.py for Social Ventures Australia (SVA), combining
-- description/content matching and tender recency.

ALTER TABLE `tenderai-dev.TenderAI.tenders`
ADD COLUMN IF NOT EXISTS relevance_score INT64;
