-- Migration: add the relevance determination columns to the tenders table
-- Run once against the live BigQuery dataset.
--
-- These are produced by processing/relevance_determination.py, which classifies
-- each processed tender against the focus area and work type taxonomies in
-- tender_processor.cfg. focus_areas and work_types are {tag_id: score 1-5} maps
-- stored as JSON (BigQuery has no MAP type); fit is the single 0-100 score
-- derived from those tag scores plus the tender's recency, floored to near zero
-- when the main deliverable is out of scope.

ALTER TABLE `tenderai-dev.TenderAI.tenders`
ADD COLUMN IF NOT EXISTS focus_areas JSON,
ADD COLUMN IF NOT EXISTS work_types JSON,
ADD COLUMN IF NOT EXISTS fit INT64,
ADD COLUMN IF NOT EXISTS fit_reason STRING;
