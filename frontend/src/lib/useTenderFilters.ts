import { useEffect, useState } from 'react';

const SEARCH_DEBOUNCE_MS = 300;

export function useTenderFilters() {
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const [jurisdiction, setJurisdiction] = useState('');
  const [status, setStatus] = useState('');
  const [year, setYear] = useState('');
  const [minDate, setMinDate] = useState('');
  const [maxDate, setMaxDate] = useState('');
  const [advancedSearch, setAdvancedSearch] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearchQuery(searchQuery);
    }, SEARCH_DEBOUNCE_MS);

    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  const handleResetFilters = () => {
    setSearchQuery('');
    setDebouncedSearchQuery('');
    setJurisdiction('');
    setStatus('');
    setYear('');
    setMinDate('');
    setMaxDate('');
    setAdvancedSearch(false);
  };

  return {
    searchQuery, setSearchQuery, debouncedSearchQuery,
    advancedSearch, setAdvancedSearch,
    showFilters, setShowFilters,
    jurisdiction, setJurisdiction,
    status, setStatus,
    year, setYear,
    minDate, setMinDate,
    maxDate, setMaxDate,
    handleResetFilters,
  };
}
