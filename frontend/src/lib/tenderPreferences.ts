export const PREFERENCE_SCHEMA_VERSION = 1 as const;

export const CONFIGURABLE_CARD_FIELDS = [
  { id: 'atAGlance', label: 'At a Glance' },
  { id: 'atmId', label: 'ATM ID' },
  { id: 'closingDate', label: 'Closing Date' },
  { id: 'agency', label: 'Agency' },
  { id: 'source', label: 'Source' },
  { id: 'monetaryValue', label: 'Monetary Value' },
  { id: 'openingDate', label: 'Opening Date' },
  { id: 'location', label: 'Location' },
  { id: 'status', label: 'Status' },
] as const;

export type CardFieldId = (typeof CONFIGURABLE_CARD_FIELDS)[number]['id'];

export const DEFAULT_COMPACT_CARD_FIELDS = Object.freeze([
  'atAGlance',
  'atmId',
  'closingDate',
  'agency',
  'source',
] as const satisfies readonly CardFieldId[]);

export interface TenderCardViewPreferences {
  compactFields: readonly CardFieldId[];
}

export type DefaultTenderStatus = '' | 'open' | 'closed' | 'awarded' | 'unknown';

export interface DefaultTenderFilters {
  jurisdiction: string;
  status: DefaultTenderStatus;
  year: string;
  minDate: string;
  maxDate: string;
}

export interface TenderPreferences {
  schemaVersion: typeof PREFERENCE_SCHEMA_VERSION;
  cardView: TenderCardViewPreferences;
  defaultFilters: DefaultTenderFilters;
}

export const DEFAULT_TENDER_CARD_VIEW_PREFERENCES: TenderCardViewPreferences = Object.freeze({
  compactFields: DEFAULT_COMPACT_CARD_FIELDS,
});

export const DEFAULT_TENDER_FILTERS: DefaultTenderFilters = Object.freeze({
  jurisdiction: '',
  status: '',
  year: '',
  minDate: '',
  maxDate: '',
});

export const DEFAULT_TENDER_PREFERENCES: TenderPreferences = Object.freeze({
  schemaVersion: PREFERENCE_SCHEMA_VERSION,
  cardView: DEFAULT_TENDER_CARD_VIEW_PREFERENCES,
  defaultFilters: DEFAULT_TENDER_FILTERS,
});

// Firestore Rules in Module 2 should enforce the same limit.
export const MAX_JURISDICTION_LENGTH = 200;

const CARD_FIELD_IDS: ReadonlySet<string> = new Set(
  CONFIGURABLE_CARD_FIELDS.map((field) => field.id),
);
const VALID_STATUSES: ReadonlySet<string> = new Set([
  '',
  'open',
  'closed',
  'awarded',
  'unknown',
]);
const YEAR_PATTERN = /^\d{4}$/;
const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return false;
  }

  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function defaultCardViewPreferences(): TenderCardViewPreferences {
  return { compactFields: [...DEFAULT_COMPACT_CARD_FIELDS] };
}

function defaultTenderFilters(): DefaultTenderFilters {
  return { ...DEFAULT_TENDER_FILTERS };
}

function defaultTenderPreferences(): TenderPreferences {
  return {
    schemaVersion: PREFERENCE_SCHEMA_VERSION,
    cardView: defaultCardViewPreferences(),
    defaultFilters: defaultTenderFilters(),
  };
}

function sanitizeJurisdiction(value: unknown): string {
  if (typeof value !== 'string') return '';

  const jurisdiction = value.trim();
  return jurisdiction.length <= MAX_JURISDICTION_LENGTH ? jurisdiction : '';
}

function sanitizeStatus(value: unknown): DefaultTenderStatus {
  return typeof value === 'string' && VALID_STATUSES.has(value)
    ? value as DefaultTenderStatus
    : '';
}

function sanitizeYear(value: unknown): string {
  return typeof value === 'string' && (value === '' || YEAR_PATTERN.test(value))
    ? value
    : '';
}

function isLeapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function sanitizeDate(value: unknown): string {
  if (typeof value !== 'string' || value === '') return '';

  const match = DATE_PATTERN.exec(value);
  if (!match) return '';

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1 || month < 1 || month > 12) return '';

  const daysInMonth = [
    31,
    isLeapYear(year) ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ];
  return day >= 1 && day <= daysInMonth[month - 1] ? value : '';
}

export function isCardFieldId(value: unknown): value is CardFieldId {
  return typeof value === 'string' && CARD_FIELD_IDS.has(value);
}

export function sanitizeTenderCardViewPreferences(
  value: unknown,
): TenderCardViewPreferences {
  if (!isPlainObject(value) || !Array.isArray(value.compactFields)) {
    return defaultCardViewPreferences();
  }

  const selectedFields = new Set<CardFieldId>();
  for (const field of value.compactFields) {
    if (isCardFieldId(field)) selectedFields.add(field);
  }

  return {
    compactFields: CONFIGURABLE_CARD_FIELDS
      .map((field) => field.id)
      .filter((field) => selectedFields.has(field)),
  };
}

export function sanitizeDefaultTenderFilters(value: unknown): DefaultTenderFilters {
  if (!isPlainObject(value)) return defaultTenderFilters();

  let minDate = sanitizeDate(value.minDate);
  let maxDate = sanitizeDate(value.maxDate);
  if (minDate && maxDate && minDate > maxDate) {
    minDate = '';
    maxDate = '';
  }

  return {
    jurisdiction: sanitizeJurisdiction(value.jurisdiction),
    status: sanitizeStatus(value.status),
    year: sanitizeYear(value.year),
    minDate,
    maxDate,
  };
}

export function sanitizeTenderPreferences(value: unknown): TenderPreferences {
  if (!isPlainObject(value) || value.schemaVersion !== PREFERENCE_SCHEMA_VERSION) {
    return defaultTenderPreferences();
  }

  return {
    schemaVersion: PREFERENCE_SCHEMA_VERSION,
    cardView: sanitizeTenderCardViewPreferences(value.cardView),
    defaultFilters: sanitizeDefaultTenderFilters(value.defaultFilters),
  };
}
