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
  states: string[];
  sourceId: string;
  focusAreas: string[];
  status: DefaultTenderStatus;
  year: string;
  minDate: string;
  maxDate: string;
  publishedAfter: string;
  publishedBefore: string;
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
  states: [],
  sourceId: '',
  focusAreas: [],
  status: '',
  year: '',
  minDate: '',
  maxDate: '',
  publishedAfter: '',
  publishedBefore: '',
});

export const DEFAULT_TENDER_PREFERENCES: TenderPreferences = Object.freeze({
  schemaVersion: PREFERENCE_SCHEMA_VERSION,
  cardView: DEFAULT_TENDER_CARD_VIEW_PREFERENCES,
  defaultFilters: DEFAULT_TENDER_FILTERS,
});

// Firestore Rules in Module 2 should enforce the same limit.
export const MAX_JURISDICTION_LENGTH = 200;
export const MAX_SOURCE_ID_LENGTH = 100;
export const MAX_FOCUS_AREA_ID_LENGTH = 100;
// The current taxonomy has 11 entries; leave one slot for a compatible addition
// while keeping Firestore Rules' explicit per-item validation within its budget.
export const MAX_FOCUS_AREAS = 12;

const VALID_STATES: ReadonlySet<string> = new Set([
  'ACT',
  'NSW',
  'NT',
  'QLD',
  'SA',
  'TAS',
  'VIC',
  'WA',
]);

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
const SOURCE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
// Keep in sync with the runtime taxonomy parser in api/app/runtime_config.py.
const FOCUS_AREA_ID_PATTERN = /^[a-z][a-z0-9_]*$/;

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
  return {
    ...DEFAULT_TENDER_FILTERS,
    states: [...DEFAULT_TENDER_FILTERS.states],
    focusAreas: [...DEFAULT_TENDER_FILTERS.focusAreas],
  };
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

function sanitizeStates(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  const states = new Set<string>();
  for (const state of value) {
    if (typeof state === 'string' && VALID_STATES.has(state)) {
      states.add(state);
      if (states.size === VALID_STATES.size) break;
    }
  }
  return [...states];
}

function sanitizeSourceId(value: unknown): string {
  if (typeof value !== 'string') return '';

  const sourceId = value.trim();
  return sourceId.length <= MAX_SOURCE_ID_LENGTH
    && (sourceId === '' || SOURCE_ID_PATTERN.test(sourceId))
    ? sourceId
    : '';
}

function sanitizeFocusAreas(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  const focusAreas = new Set<string>();
  for (const focusArea of value) {
    if (
      typeof focusArea === 'string'
      && focusArea.length <= MAX_FOCUS_AREA_ID_LENGTH
      && FOCUS_AREA_ID_PATTERN.test(focusArea)
    ) {
      focusAreas.add(focusArea);
      if (focusAreas.size === MAX_FOCUS_AREAS) break;
    }
  }
  return [...focusAreas];
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

  let publishedAfter = sanitizeDate(value.publishedAfter);
  let publishedBefore = sanitizeDate(value.publishedBefore);
  if (publishedAfter && publishedBefore && publishedAfter > publishedBefore) {
    publishedAfter = '';
    publishedBefore = '';
  }

  return {
    jurisdiction: sanitizeJurisdiction(value.jurisdiction),
    states: sanitizeStates(value.states),
    sourceId: sanitizeSourceId(value.sourceId),
    focusAreas: sanitizeFocusAreas(value.focusAreas),
    status: sanitizeStatus(value.status),
    year: sanitizeYear(value.year),
    minDate,
    maxDate,
    publishedAfter,
    publishedBefore,
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
