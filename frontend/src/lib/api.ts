import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const TENDERS_ENDPOINT_URL = import.meta.env.VITE_TENDERS_ENDPOINT_URL ?? `${API_BASE_URL}/tenders`;

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
  q?: string;
  status?: string;
  category?: string;
  location?: string;
  year?: string;
  min_value?: number;
  max_value?: number;
  closing_after?: string;
  closing_before?: string;
}

export async function getTenders(params: GetTendersParams = {}): Promise<Tender[]> {
  const url = TENDERS_ENDPOINT_URL;
  try {
    const { data } = await axios.get<Tender[]>(url, {
      params: {
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
        q: params.q || undefined,
        status: params.status || undefined,
        category: params.category || undefined,
        location: params.location || undefined,
        year: params.year || undefined,
        min_value: params.min_value ?? undefined,
        max_value: params.max_value ?? undefined,
        closing_after: params.closing_after || undefined,
        closing_before: params.closing_before || undefined,
      },
    });
    return data;
  } catch (err) {
    const detail = axios.isAxiosError(err) ? err.message : 'unknown error';
    throw new Error(`Couldn't load tenders from ${url} (${detail}). Is the API running?`);
  }
}

export async function getLocations(): Promise<string[]> {
  const url = `${API_BASE_URL}/locations`;
  try {
    const { data } = await axios.get<string[]>(url);
    return data;
  } catch (err) {
    console.error(`Failed to load locations from ${url}`, err);
    return [];
  }
}
