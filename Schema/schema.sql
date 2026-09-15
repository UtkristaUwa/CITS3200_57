-- ---------------------------------------------------------------------
-- 1. sources — the list of portals being monitored.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `tenderai-dev.TenderAI.sources` (
  source_id                STRING    NOT NULL,   -- e.g. "wa-tenders-gov-au"
  name                     STRING,                -- display name
  base_url                 STRING,
  jurisdiction             STRING,                -- WA / NSW / Federal / Local-Cannington etc.
  auth_type                STRING,                -- none | login | login+mfa_email
  is_active                BOOL,
  last_successful_scan_at  TIMESTAMP,
  notes                    STRING
);

-- ---------------------------------------------------------------------
-- 2. search_profiles — org interest areas used for relevance matching.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `tenderai-dev.TenderAI.search_profiles` (
  profile_id            STRING    NOT NULL,
  name                  STRING,                   -- e.g. "Early Childhood"
  keywords              ARRAY<STRING>,
  semantic_criteria     STRING,                    -- free-text description for semantic matching
  exclusions            ARRAY<STRING>,
  relevance_threshold   FLOAT64,
  notify_email          BOOL,
  notify_teams          BOOL,
  is_active             BOOL
);

-- ---------------------------------------------------------------------
-- 3. tenders — current/live snapshot. One row per opportunity.
--    Raw extracted fields only — no AI output lives here (see #5).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `tenderai-dev.TenderAI.tenders` (
  tender_id             STRING    NOT NULL,   -- internal surrogate key (UUID)
  source_reference_id   STRING,                -- the portal's own ID/code
  source_id             STRING,                -- FK -> sources.source_id
  source_url            STRING,                -- direct link back to the listing

  title                 STRING,
  issuing_agency        STRING,                -- the party posting the tender
  category              STRING,                -- tender | rfq | eoi | grant
  status                STRING,                -- open | closed | awarded | unknown

  publish_date          DATE,
  closing_date          DATE,

  value_amount          NUMERIC,
  value_currency        STRING,
  value_notes           STRING,                -- e.g. "not disclosed", "$50k-$100k range"

  location              STRING,                -- jurisdiction / region the work applies to
  description           STRING,                -- raw extracted description text

  contact_name          STRING,
  contact_email         STRING,
  contact_phone         STRING,
  lodgment_address      STRING,

  documents             ARRAY<STRUCT<
    document_id         STRING,
    file_name            STRING,
    file_type            STRING,               -- pdf | docx | rtf | other
    extracted_text       STRING,               -- plain-text extraction; no original file is archived
    parsed_at            TIMESTAMP
  >>,

  content_hash          STRING,                -- hash of extracted fields, for change detection
  first_seen_at         TIMESTAMP,             -- when this tender was first discovered
  last_scanned_at       TIMESTAMP,             -- bumped every scan, changed or not
  updated_at            TIMESTAMP,             -- bumped only when content actually changes

  raw_extra             JSON                   -- catch-all for source-specific fields not yet modeled
)
PARTITION BY DATE(first_seen_at)
CLUSTER BY source_id, status;

-- ---------------------------------------------------------------------
-- 4. tender_snapshots — append-only history. One row per DETECTED CHANGE,
--    not per scan. 
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `tenderai-dev.TenderAI.tender_snapshots` (
  snapshot_id       STRING    NOT NULL,
  tender_id         STRING    NOT NULL,   
  scanned_at        TIMESTAMP NOT NULL,
  content_hash      STRING,
  changed_fields    ARRAY<STRING>,        -- e.g. ["closing_date", "value_amount"]
  raw_payload       JSON                  -- full extracted record at this point in time
)
PARTITION BY DATE(scanned_at)
CLUSTER BY tender_id;

-- ---------------------------------------------------------------------
-- 5. tender_enrichment — AI-generated layer, kept separate so re-running
--    summarization (new prompt/model) never touches raw extracted data.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `tenderai-dev.TenderAI.tender_enrichment` (
  enrichment_id         STRING    NOT NULL,
  tender_id             STRING    NOT NULL,   
  generated_at          TIMESTAMP,
  model_used            STRING,
  prompt_version        STRING,

  summary               STRING,                -- 3-line summary
  key_points            ARRAY<STRING>,          -- deliverables/outcomes bullets

  matched_profile_ids   ARRAY<STRING>,          -- FKs -> search_profiles.profile_id
  relevance_scores      ARRAY<STRUCT<
    profile_id  STRING,
    score        FLOAT64
  >>,

  is_current            BOOL
)
PARTITION BY DATE(generated_at)
CLUSTER BY tender_id;

-- ---------------------------------------------------------------------
-- 6. user_tender_status — per-user interaction state. Not a property of
--    the tender itself, so it lives separately.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `tenderai-dev.TenderAI.user_tender_status` (
  user_id       STRING    NOT NULL,
  tender_id     STRING    NOT NULL,   -- FK -> tenders.tender_id
  is_starred    BOOL,
  decision      STRING,                -- accepted | rejected | pending
  feedback      STRING,
  updated_at    TIMESTAMP
)
CLUSTER BY user_id, tender_id;

-- ---------------------------------------------------------------------
-- 7. processing_log — per-run health per source.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `tenderai-dev.TenderAI.processing_log` (
  log_id             STRING    NOT NULL,
  source_id          STRING,                  -- FK -> sources.source_id
  run_started_at     TIMESTAMP,
  run_finished_at    TIMESTAMP,
  status             STRING,                   -- success | partial | failed
  error_message      STRING,
  tenders_found      INT64,
  tenders_new        INT64,
  tenders_updated    INT64
)
PARTITION BY DATE(run_started_at)
CLUSTER BY source_id;
