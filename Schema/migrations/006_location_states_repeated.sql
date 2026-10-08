-- Migration: replace location_state (STRING) with location_states (ARRAY<STRING>)
-- Run once against the live BigQuery dataset.
--
-- Plenty of tenders genuinely cover more than one state, which the single STRING
-- column from 005 could only express with a "MULTI" placeholder that told the
-- front end nothing about WHICH states. location_states holds them properly:
-- WA | NSW | VIC | QLD | SA | TAS | ACT | NT, or NATIONAL on its own, and empty
-- when the source doesn't support an answer.
--
-- A column's mode can't be changed from NULLABLE to REPEATED in place, which is
-- why this adds a new column rather than altering 005's. Nothing read
-- location_state yet, so it is dropped rather than migrated; if the column was
-- never created (005 unapplied), the DROP is skipped by IF EXISTS.

ALTER TABLE `tenderai-dev.TenderAI.tenders`
ADD COLUMN IF NOT EXISTS location_states ARRAY<STRING>;

ALTER TABLE `tenderai-dev.TenderAI.tenders`
DROP COLUMN IF EXISTS location_state;
