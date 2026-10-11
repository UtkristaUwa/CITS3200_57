import { useEffect, useRef, useState } from 'react';
import { useAuth } from './AuthContext';
import { useUserPreferences } from './UserPreferencesContext';
import type { DefaultTenderFilters, DefaultTenderStatus } from './tenderPreferences';

const SEARCH_DEBOUNCE_MS = 300;

function defaultFiltersMatch(
  first: DefaultTenderFilters,
  second: DefaultTenderFilters,
): boolean {
  return first.jurisdiction === second.jurisdiction
    && first.status === second.status
    && first.year === second.year
    && first.minDate === second.minDate
    && first.maxDate === second.maxDate;
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
  // Kept so the saved-defaults payload keeps the exact shape firestore.rules
  // validates. It has no control behind it any more -- the state multi-select
  // replaced it -- and is deliberately not applied to the query, so a value
  // saved before that change can't narrow results invisibly.
  const [jurisdiction, setJurisdiction] = useState('');
  // Session-only: not part of tenderPreferences, so no schema or rules change.
  const [states, setStates] = useState<string[]>([]);
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
    status,
    year,
    minDate,
    maxDate,
  });

  currentUidRef.current = currentUid;
  currentDefaultFiltersRef.current = {
    jurisdiction,
    status,
    year,
    minDate,
    maxDate,
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
    saveRequestTokenRef.current += 1;
    setSearchQuery('');
    setDebouncedSearchQuery('');
    setShowFilters(false);
    setJurisdiction('');
    setStatus('');
    setYear('');
    setMinDate('');
    setMaxDate('');
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
    setStatus(defaultFilters.status);
    setYear(defaultFilters.year);
    setMinDate(defaultFilters.minDate);
    setMaxDate(defaultFilters.maxDate);
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
    setStatus('');
    setYear('');
    setMinDate('');
    setMaxDate('');
    setAdvancedSearch(false);
    setDefaultFiltersSaveError(null);
    setDefaultFiltersSaved(false);
  };

  const setSavedJurisdiction = (value: string) => {
    setJurisdiction(value);
    setDefaultFiltersSaved(false);
  };

  const setSavedStatus = (value: DefaultTenderStatus) => {
    setStatus(value);
    setDefaultFiltersSaved(false);
  };

  const setSavedYear = (value: string) => {
    setYear(value);
    setDefaultFiltersSaved(false);
  };

  const setSavedMinDate = (value: string) => {
    setMinDate(value);
    setDefaultFiltersSaved(false);
  };

  const setSavedMaxDate = (value: string) => {
    setMaxDate(value);
    setDefaultFiltersSaved(false);
  };

  const handleSaveDefaultFilters = async () => {
    if (!filtersReady || defaultFiltersSaving) return;

    const originatingUid = currentUid;
    const submittedFilters = { ...currentDefaultFiltersRef.current };
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

    const requestToken = saveRequestTokenRef.current + 1;
    saveRequestTokenRef.current = requestToken;
    setSavingDefaultFilters(true);
    setDefaultFiltersSaveError(null);
    setDefaultFiltersSaved(false);

    try {
      await saveDefaultTenderFilters(submittedFilters);
      if (
        currentUidRef.current === originatingUid
        && saveRequestTokenRef.current === requestToken
        && defaultFiltersMatch(currentDefaultFiltersRef.current, submittedFilters)
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
    jurisdiction, setJurisdiction: setSavedJurisdiction,
    states, setStates,
    status, setStatus: setSavedStatus,
    year, setYear: setSavedYear,
    minDate, setMinDate: setSavedMinDate,
    maxDate, setMaxDate: setSavedMaxDate,
    filtersReady,
    defaultFiltersSaving,
    defaultFiltersLoadError: preferencesError?.message ?? null,
    defaultFiltersSaveError,
    defaultFiltersSaved,
    handleSaveDefaultFilters,
    handleResetFilters,
  };
}
