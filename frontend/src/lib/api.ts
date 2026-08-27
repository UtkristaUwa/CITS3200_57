import axios from 'axios';

// Matches api/app/config.py's Settings.allowed_origins default and
// DEVELOPMENT.md's documented local API port. Override by setting
// VITE_API_BASE_URL in frontend/.env.local for anything else (e.g. a
// deployed Cloud Run URL).
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const TENDERS_ENDPOINT_URL = import.meta.env.VITE_TENDERS_ENDPOINT_URL ?? `${API_BASE_URL}/tenders`;

// Mirrors api/app/models.py's DocumentOut/TenderOut exactly — keep these
// two in sync manually if the backend model changes, the same way
// api/app/bigquery.py's ALL_COLUMNS is kept in sync with ingestion/'s.
export interface TenderDocument {
  document_id: string | null;
  file_name: string;
  file_type: string | null;
  extracted_text: string | null;
  parsed_at: string | null;
}

export interface Tender {
  tender_id: string;
  source_reference_id: string | null;
  source_id: string | null;
  source_url: string | null;

  title: string;
  issuing_agency: string | null;
  category: string | null;
  status: string | null;

  publish_date: string | null;
  closing_date: string | null;

  value_amount: number | null;
  value_currency: string | null;
  value_notes: string | null;

  location: string | null;
  description: string | null;

  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  lodgment_address: string | null;

  documents: TenderDocument[];

  content_hash: string | null;
  first_seen_at: string;
  last_scanned_at: string;
  updated_at: string;

  raw_extra: Record<string, unknown> | null;
}

export interface GetTendersParams {
  limit?: number;
  offset?: number;
}

/**
 * GET /tenders — see api/app/routers/tenders.py. Throws a plain Error with
 * a message that names the URL it tried, so a failure renders as something
 * a teammate can act on ("is the backend running?") instead of a raw axios
 * stack trace.
 */
export async function getTenders(params: GetTendersParams = {}): Promise<Tender[]> {
  const url = TENDERS_ENDPOINT_URL;
  try {
    const { data } = await axios.get<Tender[]>(url, {
      params: {
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      },
    });
    return data;
  } catch (err) {
    const detail = axios.isAxiosError(err) ? err.message : 'unknown error';
    throw new Error(`Couldn't load tenders from ${url} (${detail}). Is the API running?`);
  }
}
