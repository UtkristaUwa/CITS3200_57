import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { auth } from './firebase';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const TENDERS_ENDPOINT_URL = import.meta.env.VITE_TENDERS_ENDPOINT_URL ?? `${API_BASE_URL}/tenders`;

let cachedLocations: string[] | null = null;
let locationsRequestPromise: Promise<string[]> | null = null;

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

export interface ModelConfigResponse {
  triage_model: string;
  extraction_model: string;
  generation: string;
}

export interface ModelConfigUpdate {
  triage_model?: string;
  extraction_model?: string;
  generation: string;
}

export async function getModelConfig(): Promise<ModelConfigResponse> {
  const { data } = await http.get<ModelConfigResponse>(`${API_BASE_URL}/admin/config/models`);
  return data;
}

export async function updateModelConfig(update: ModelConfigUpdate): Promise<ModelConfigResponse> {
  const { data } = await http.patch<ModelConfigResponse>(`${API_BASE_URL}/admin/config/models`, update);
  return data;
}

export interface RelevanceConfigResponse {
  classification_thoughts: string;
  focus_areas: string;
  work_types: string;
  out_of_scope: string;
  focus_area_weight: number;
  work_type_weight: number;
  recency_weight: number;
  recency_horizon_days: number;
  out_of_scope_fit_cap: number;
  max_focus_areas: number;
  max_work_types: number;
  relevance_model: string;
  relevance_temperature: number;
  generation: string;
}

export interface RelevanceConfigUpdate {
  classification_thoughts?: string;
  focus_areas?: string;
  work_types?: string;
  out_of_scope?: string;
  focus_area_weight?: number;
  work_type_weight?: number;
  recency_weight?: number;
  recency_horizon_days?: number;
  out_of_scope_fit_cap?: number;
  max_focus_areas?: number;
  max_work_types?: number;
  relevance_model?: string;
  relevance_temperature?: number;
  generation: string;
}

export async function getRelevanceConfig(): Promise<RelevanceConfigResponse> {
  const { data } = await http.get<RelevanceConfigResponse>(`${API_BASE_URL}/admin/config/relevance`);
  return data;
}

export async function updateRelevanceConfig(
  update: RelevanceConfigUpdate,
): Promise<RelevanceConfigResponse> {
  const { data } = await http.patch<RelevanceConfigResponse>(
    `${API_BASE_URL}/admin/config/relevance`,
    update,
  );
  return data;
}

export interface TenderDocument {
  document_id: string | null;
  file_name: string;
  file_type: string | null;
  extracted_text: string | null;
  parsed_at: string | null;
  storage_url: string | null;
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
  distance?: number | null;
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

export function isRequestCancelled(error: unknown): boolean {
  return axios.isCancel(error);
}

export async function getTenders(
  params: GetTendersParams = {},
  signal?: AbortSignal,
): Promise<Tender[]> {
  const url = TENDERS_ENDPOINT_URL;
  try {
    const { data } = await http.get<Tender[]>(url, {
      signal,
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
    if (isRequestCancelled(err)) throw err;
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

export async function getDocumentBlob(storageUrl: string, filename: string): Promise<Blob> {
  const { data } = await http.get<Blob>(`${API_BASE_URL}/documents/download`, {
    params: { storage_url: storageUrl, filename },
    responseType: 'blob',
  });
  return data;
}

/** Thrown by getTenderDocumentsZip with a message that can be shown as-is. */
export class ZipDownloadError extends Error {}

const ZIP_INTERRUPTED = 'The download was interrupted before it finished. Please try again.';

// A zip ends with its end-of-central-directory record: 22 bytes starting
// "PK\x05\x06" when, as with the API's archives, there is no comment. When a
// file fails mid-way the API aborts the stream instead of finishing it, so a
// body without that record is a truncated download, never a zip to save.
async function isCompleteZip(blob: Blob): Promise<boolean> {
  if (blob.size < 22) return false;
  const tail = new Uint8Array(await blob.slice(blob.size - 22, blob.size - 18).arrayBuffer());
  return tail[0] === 0x50 && tail[1] === 0x4b && tail[2] === 0x05 && tail[3] === 0x06;
}

// Error bodies arrive as a Blob because of responseType: 'blob'; the API's
// `detail` says what went wrong (e.g. which file is missing from storage).
async function zipErrorMessage(err: unknown): Promise<string> {
  if (!axios.isAxiosError(err) || !err.response) return ZIP_INTERRUPTED;
  const body: unknown = err.response.data;
  if (body instanceof Blob) {
    try {
      const detail: unknown = JSON.parse(await body.text())?.detail;
      if (typeof detail === 'string' && detail) return detail;
    } catch {
      // Not JSON: fall through to the generic message.
    }
  }
  return `The documents couldn't be downloaded (error ${err.response.status}). Please try again.`;
}

/**
 * GET /tenders/{id}/documents/zip — every stored attachment of one tender as a
 * single zip, built server-side from the tender's own document list. Resolves
 * only with a complete archive; anything else throws ZipDownloadError.
 */
export async function getTenderDocumentsZip(tenderId: string): Promise<Blob> {
  let data: Blob;
  try {
    ({ data } = await http.get<Blob>(
      `${API_BASE_URL}/tenders/${encodeURIComponent(tenderId)}/documents/zip`,
      { responseType: 'blob' },
    ));
  } catch (err) {
    throw new ZipDownloadError(await zipErrorMessage(err), { cause: err });
  }
  if (!(await isCompleteZip(data))) throw new ZipDownloadError(ZIP_INTERRUPTED);
  return data;
}

export function getLocations(): Promise<string[]> {
  if (cachedLocations) return Promise.resolve([...cachedLocations]);
  if (locationsRequestPromise) return locationsRequestPromise;

  const url = `${API_BASE_URL}/locations`;
  locationsRequestPromise = http.get<string[]>(url)
    .then(({ data }) => {
      cachedLocations = [...data];
      return [...cachedLocations];
    })
    .catch((err: unknown) => {
      console.error(`Failed to load locations from ${url}`, err);
      return [];
    })
    .finally(() => {
      locationsRequestPromise = null;
    });

  return locationsRequestPromise;
}

export interface ScraperHealthRecord {
  website: string;
  url: string;
  last_run: string;
  status: string;
  status_color: 'success' | 'error' | 'warning' | 'default';
  message: string;
}

export async function getScraperHealth(): Promise<ScraperHealthRecord[]> {
  const { data } = await http.get<ScraperHealthRecord[]>(`${API_BASE_URL}/health/scrapers`);
  return data;
}

