/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_TENDERS_ENDPOINT_URL?: string;
  /** Directory (tenant) ID of the client's Entra tenant — see docs/SSO_SETUP.md. */
  readonly VITE_ENTRA_TENANT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
