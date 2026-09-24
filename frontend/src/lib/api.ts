import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { auth } from './firebase';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const TENDERS_ENDPOINT_URL = import.meta.env.VITE_TENDERS_ENDPOINT_URL ?? `${API_BASE_URL}/tenders`;

// Every endpoint except /health sits behind app/auth.py's current_user
// dependency, which wants the caller's Firebase ID token. Attaching it here
// rather than at each call site means a new endpoint is authenticated by
// default instead of by remembering to. The token is a short-lived JWT, not a
// password: getIdToken() serves it from cache and refreshes it when it is
// close to expiring.
const http = axios.create();

http.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  const user = auth.currentUser;
  if (user) {
    config.headers.set('Authorization', `Bearer ${await user.getIdToken()}`);
  }
  return config;
});

// A 401 here almost always means the cached token expired mid-session (for
// example the laptop was asleep). Force a refresh and replay the request once;
// a second 401 is a real authentication failure and is surfaced to the caller.
http.interceptors.response.use(undefined, async (error: AxiosError) => {
  const config = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;
  const user = auth.currentUser;

  if (error.response?.status === 401 && config && !config._retried && user) {
    config._retried = true;
    config.headers.set('Authorization', `Bearer ${await user.getIdToken(true)}`);
    return http.request(config);
  }
  return Promise.reject(error);
});

/** The caller's profile as the API sees it — see api/app/routers/auth.py. */
export interface Me {
  uid: string;
  email: string | null;
  provider: 'microsoft.com' | 'password';
  isAdmin: boolean;
  status: string;
}

/**
 * GET /auth/me. Called once after sign-in. For a tenant member arriving
 * through Entra SSO for the first time this is also what provisions their
 * Firestore profile, so it runs before anything reads users/{uid}.
 */
export async function getMe(): Promise<Me> {
  const { data } = await http.get<Me>(`${API_BASE_URL}/auth/me`);
  return data;
}

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
  summary_headline: string | null;
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
    const { data } = await http.get<Tender[]>(url, {
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
    if (axios.isAxiosError(err) && err.response) {
      if (err.response.status === 401) {
        throw new Error('Your session has expired. Please sign in again.');
      }
      if (err.response.status === 403) {
        throw new Error("This account isn't authorised to use TenderAI. Ask an admin to grant access.");
      }
    }
    const detail = axios.isAxiosError(err) ? err.message : 'unknown error';
    throw new Error(`Couldn't load tenders from ${url} (${detail}). Is the API running?`);
  }
}

export async function getLocations(): Promise<string[]> {
  const url = `${API_BASE_URL}/locations`;
  try {
    const { data } = await http.get<string[]>(url);
    return data;
  } catch (err) {
    console.error(`Failed to load locations from ${url}`, err);
    return [];
  }
}
