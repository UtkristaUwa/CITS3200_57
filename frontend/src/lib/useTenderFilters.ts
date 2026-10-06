import { useState } from 'react';

export function useTenderFilters() {
  const [searchQuery, setSearchQuery] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const [jurisdiction, setJurisdiction] = useState('');
  const [category, setCategory] = useState('');
  const [status, setStatus] = useState('');
  const [year, setYear] = useState('');
  const [minDate, setMinDate] = useState('');
  const [maxDate, setMaxDate] = useState('');

  const handleResetFilters = () => {
    setSearchQuery('');
    setJurisdiction('');
    setCategory('');
    setStatus('');
    setYear('');
    setMinDate('');
    setMaxDate('');
  };

  return {
    searchQuery, setSearchQuery,
    showFilters, setShowFilters,
    jurisdiction, setJurisdiction,
    category, setCategory,
    status, setStatus,
    year, setYear,
    minDate, setMinDate,
    maxDate, setMaxDate,
    handleResetFilters,
  };
}
