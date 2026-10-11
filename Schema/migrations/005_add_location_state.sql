-- Migration: add location_state to the tenders table
-- Run once against the live BigQuery dataset.
--
-- `location` stays exactly as the source wrote it, which is why it is unusable
-- as a filter: the same state arrives as "WA", "Western Australia", a region, or
-- a street address. location_state is the filterable companion, one of
-- WA | NSW | VIC | QLD | SA | TAS | ACT | NT | NATIONAL | MULTI, or NULL when
-- the source doesn't support an answer.
--
-- It is resolved in processing/tender_processor.py: the location's postcode
-- decides the state wherever there is one (postcodes map to states
-- deterministically), and the model's own answer is only used when there is no
-- postcode, so an ambiguous suburb name stays NULL instead of being guessed.

ALTER TABLE `tenderai-dev.TenderAI.tenders`
ADD COLUMN IF NOT EXISTS location_state STRING;
