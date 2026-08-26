
CREATE OR REPLACE TABLE `tenderai-dev.TenderAI.tenders`
PARTITION BY DATE(first_seen_at)
CLUSTER BY source_id, status
AS
SELECT
  tender_id,
  source_reference_id,
  source_id,
  source_url,
  title,
  issuing_agency,
  category,
  status,
  publish_date,
  closing_date,
  value_amount,
  value_currency,
  value_notes,
  location,
  description,
  contact_name,
  contact_email,
  contact_phone,
  lodgment_address,
  ARRAY(
    SELECT AS STRUCT
      d.document_id,
      d.file_name,
      d.file_type,
      CAST(NULL AS STRING)    AS extracted_text,
      CAST(NULL AS TIMESTAMP) AS parsed_at
    FROM UNNEST(documents) AS d
  ) AS documents,
  content_hash,
  first_seen_at,
  last_scanned_at,
  updated_at,
  raw_extra
FROM `tenderai-dev.TenderAI.tenders`;
