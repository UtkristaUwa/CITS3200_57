import { useEffect, useRef, useState } from 'react';
import { useAuth } from './AuthContext';
import { useUserPreferences } from './UserPreferencesContext';
import {
  sanitizeDefaultTenderFilters,
  type DefaultTenderFilters,
  type DefaultTenderStatus,
} from './tenderPreferences';

const SEARCH_DEBOUNCE_MS = 300;

function defaultFiltersMatch(
  first: DefaultTenderFilters,
  second: DefaultTenderFilters,
): boolean {
  return first.jurisdiction === second.jurisdiction
    && first.states.length === second.states.length
    && first.states.every((state, index) => state === second.states[index])
    && first.sourceId === second.sourceId
    && first.focusAreas.length === second.focusAreas.length
    && first.focusAreas.every((focusArea, index) => focusArea === second.focusAreas[index])
    && first.status === second.status
    && first.year === second.year
    && first.minDate === second.minDate
    && first.maxDate === second.maxDate
    && first.publishedAfter === second.publishedAfter
    && first.publishedBefore === second.publishedBefore;
}

export function useTenderFilters() {
  const { user, loading: authLoading } = useAuth();
  const {
    preferences,
    ready: preferencesReady,
    loading: preferencesLoading,
    saving: preferencesSaving,
    error: preferencesError,
    saveDefaultTenderFilters,
  } = useUserPreferences();
  const currentUid = user?.uid ?? null;
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  // Retained for schema-v1 compatibility. It has no control behind it any more
  // and is deliberately not applied to the query, so a legacy value cannot
  // narrow results invisibly.
  const [jurisdiction, setJurisdiction] = useState('');
  const [states, setStates] = useState<string[]>([]);
  const [focusAreas, setFocusAreas] = useState<string[]>([]);
  const [sourceId, setSourceId] = useState('');
  const [publishedAfter, setPublishedAfter] = useState('');
  const [publishedBefore, setPublishedBefore] = useState('');
  const [sortBy, setSortByState] = useState<string>('closing_date_asc');
  const lastBrowseSortRef = useRef<string>('closing_date_asc');

  const setSortBy = (val: string) => {
    if (val !== 'relevance') {
      lastBrowseSortRef.current = val;
    }
    setSortByState(val);
  };
  const [status, setStatus] = useState<DefaultTenderStatus>('');
  const [year, setYear] = useState('');
  const [minDate, setMinDate] = useState('');
  const [maxDate, setMaxDate] = useState('');
  const [advancedSearch, setAdvancedSearch] = useState(false);
  const [filtersOwnerUid, setFiltersOwnerUid] = useState<string | null>(null);
  const [savingDefaultFilters, setSavingDefaultFilters] = useState(false);
  const [defaultFiltersSaveError, setDefaultFiltersSaveError] = useState<string | null>(null);
  const [defaultFiltersSaved, setDefaultFiltersSaved] = useState(false);
  const saveRequestTokenRef = useRef(0);
  const currentUidRef = useRef(currentUid);
  const currentDefaultFiltersRef = useRef<DefaultTenderFilters>({
    jurisdiction,
    states,
    sourceId,
    focusAreas,
    status,
    year,
    minDate,
    maxDate,
    publishedAfter,
    publishedBefore,
  });

  currentUidRef.current = currentUid;
  currentDefaultFiltersRef.current = {
    jurisdiction,
    states,
    sourceId,
    focusAreas,
    status,
    year,
    minDate,
    maxDate,
    publishedAfter,
    publishedBefore,
  };

  const filtersReady = currentUid !== null
    && !authLoading
    && !preferencesLoading
    && preferencesReady
    && filtersOwnerUid === currentUid;
  const defaultFiltersSaving = savingDefaultFilters || preferencesSaving;

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearchQuery(searchQuery);
    }, SEARCH_DEBOUNCE_MS);

    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    const trimmed = searchQuery.trim();
    if (trimmed) {
      setSortByState((prev) => (prev === 'relevance' ? prev : 'relevance'));
    } else {
      setSortByState(lastBrowseSortRef.current || 'closing_date_asc');
    }
  }, [searchQuery]);

  useEffect(() => {
    saveRequestTokenRef.current += 1;
    setSearchQuery('');
    setDebouncedSearchQuery('');
    setShowFilters(false);
    setJurisdiction('');
    setStates([]);
    setFocusAreas([]);
    setSourceId('');
    setStatus('');
    setYear('');
    setMinDate('');
    setMaxDate('');
    setPublishedAfter('');
    setPublishedBefore('');
    setSortByState('closing_date_asc');
    lastBrowseSortRef.current = 'closing_date_asc';
    setAdvancedSearch(false);
    setFiltersOwnerUid(null);
    setSavingDefaultFilters(false);
    setDefaultFiltersSaveError(null);
    setDefaultFiltersSaved(false);
  }, [currentUid]);

  useEffect(() => {
    if (
      !currentUid
      || authLoading
      || preferencesLoading
      || !preferencesReady
      || filtersOwnerUid === currentUid
    ) {
      return;
    }

    const defaultFilters = preferences.defaultFilters;
    setJurisdiction(defaultFilters.jurisdiction);
    setStates(defaultFilters.states);
    setSourceId(defaultFilters.sourceId);
    setFocusAreas(defaultFilters.focusAreas);
    setStatus(defaultFilters.status);
    setYear(defaultFilters.year);
    setMinDate(defaultFilters.minDate);
    setMaxDate(defaultFilters.maxDate);
    setPublishedAfter(defaultFilters.publishedAfter);
    setPublishedBefore(defaultFilters.publishedBefore);
    setFiltersOwnerUid(currentUid);
  }, [
    authLoading,
    currentUid,
    filtersOwnerUid,
    preferences.defaultFilters,
    preferencesLoading,
    preferencesReady,
  ]);

  useEffect(() => () => {
    saveRequestTokenRef.current += 1;
  }, []);

  const handleResetFilters = () => {
    setSearchQuery('');
    setDebouncedSearchQuery('');
    setJurisdiction('');
    setStates([]);
    setFocusAreas([]);
    setSourceId('');
    setStatus('');
    setYear('');
    setMinDate('');
    setMaxDate('');
    setPublishedAfter('');
    setPublishedBefore('');
    setSortByState('closing_date_asc');
    lastBrowseSortRef.current = 'closing_date_asc';
    setAdvancedSearch(false);
    setDefaultFiltersSaveError(null);
    setDefaultFiltersSaved(false);
  };

  const setSavedJurisdiction = (value: string) => {
    setJurisdiction(value);
    setDefaultFiltersSaved(false);
    setDefaultFiltersSaveError(null);
  };

  const setSavedStates = (value: string[]) => {
    setStates(value);
    setDefaultFiltersSaved(false);
    setDefaultFiltersSaveError(null);
  };

  const setSavedSourceId = (value: string) => {
    setSourceId(value);
    setDefaultFiltersSaved(false);
    setDefaultFiltersSaveError(null);
  };

  const setSavedFocusAreas = (value: string[]) => {
    setFocusAreas(value);
    setDefaultFiltersSaved(false);
    setDefaultFiltersSaveError(null);
  };

  const setSavedStatus = (value: DefaultTenderStatus) => {
    setStatus(value);
    setDefaultFiltersSaved(false);
    setDefaultFiltersSaveError(null);
  };

  const setSavedYear = (value: string) => {
    setYear(value);
    setDefaultFiltersSaved(false);
    setDefaultFiltersSaveError(null);
  };

  const setSavedMinDate = (value: string) => {
    setMinDate(value);
    setDefaultFiltersSaved(false);
    setDefaultFiltersSaveError(null);
  };

  const setSavedMaxDate = (value: string) => {
    setMaxDate(value);
    setDefaultFiltersSaved(false);
    setDefaultFiltersSaveError(null);
  };

  const setSavedPublishedAfter = (value: string) => {
    setPublishedAfter(value);
    setDefaultFiltersSaved(false);
    setDefaultFiltersSaveError(null);
  };

  const setSavedPublishedBefore = (value: string) => {
    setPublishedBefore(value);
    setDefaultFiltersSaved(false);
    setDefaultFiltersSaveError(null);
  };

  const handleSaveDefaultFilters = async () => {
    if (!filtersReady || defaultFiltersSaving) return;

    const originatingUid = currentUid;
    const submittedFilters = {
      ...currentDefaultFiltersRef.current,
      states: [...currentDefaultFiltersRef.current.states],
      focusAreas: [...currentDefaultFiltersRef.current.focusAreas],
    };
    if (!originatingUid) return;

    if (
      submittedFilters.minDate
      && submittedFilters.maxDate
      && submittedFilters.minDate > submittedFilters.maxDate
    ) {
      setDefaultFiltersSaveError(
        'Closing After date cannot be later than Closing Before date.',
      );
      setDefaultFiltersSaved(false);
      return;
    }

    if (
      submittedFilters.publishedAfter
      && submittedFilters.publishedBefore
      && submittedFilters.publishedAfter > submittedFilters.publishedBefore
    ) {
      setDefaultFiltersSaveError(
        'Published After date cannot be later than Published Before date.',
      );
      setDefaultFiltersSaved(false);
      return;
    }

    const sanitizedFilters = sanitizeDefaultTenderFilters(submittedFilters);
    if (!defaultFiltersMatch(sanitizedFilters, submittedFilters)) {
      setDefaultFiltersSaveError(
        'One or more filter values are invalid and could not be saved.',
      );
      setDefaultFiltersSaved(false);
      return;
    }

    const requestToken = saveRequestTokenRef.current + 1;
    saveRequestTokenRef.current = requestToken;
    setSavingDefaultFilters(true);
    setDefaultFiltersSaveError(null);
    setDefaultFiltersSaved(false);

    try {
      await saveDefaultTenderFilters(sanitizedFilters);
      if (
        currentUidRef.current === originatingUid
        && saveRequestTokenRef.current === requestToken
        && defaultFiltersMatch(currentDefaultFiltersRef.current, sanitizedFilters)
      ) {
        setDefaultFiltersSaved(true);
      }
    } catch (error: unknown) {
      if (
        currentUidRef.current === originatingUid
        && saveRequestTokenRef.current === requestToken
      ) {
        setDefaultFiltersSaveError(
          error instanceof Error
            ? error.message
            : 'Failed to save default filters.',
        );
      }
    } finally {
      if (
        currentUidRef.current === originatingUid
        && saveRequestTokenRef.current === requestToken
      ) {
        setSavingDefaultFilters(false);
      }
    }
  };

  return {
    searchQuery, setSearchQuery, debouncedSearchQuery,
    advancedSearch, setAdvancedSearch,
    showFilters, setShowFilters,
    sortBy, setSortBy,
    states, setStates: setSavedStates,
    focusAreas, setFocusAreas: setSavedFocusAreas,
    sourceId, setSourceId: setSavedSourceId,
    jurisdiction, setJurisdiction: setSavedJurisdiction,
    status, setStatus: setSavedStatus,
    year, setYear: setSavedYear,
    minDate, setMinDate: setSavedMinDate,
    maxDate, setMaxDate: setSavedMaxDate,
    publishedAfter, setPublishedAfter: setSavedPublishedAfter,
    publishedBefore, setPublishedBefore: setSavedPublishedBefore,
    filtersReady,
    defaultFiltersSaving,
    defaultFiltersLoadError: preferencesError?.message ?? null,
    defaultFiltersSaveError,
    defaultFiltersSaved,
    handleSaveDefaultFilters,
    handleResetFilters,
  };
}
