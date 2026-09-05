import { useState, useMemo } from 'react';
import type { Tender } from './api';

export function useTenderFilters(initialTenders: Tender[]) {
  // --- UI State for Search & Filters ---
  const [searchQuery, setSearchQuery] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  
  // States for Dropdowns
  const [jurisdiction, setJurisdiction] = useState('');
  const [category, setCategory] = useState('');
  const [status, setStatus] = useState('');
  const [year, setYear] = useState('');
  
  // States for Ranges (Date and Value)
  const [minDate, setMinDate] = useState('');
  const [maxDate, setMaxDate] = useState('');
  const [minValue, setMinValue] = useState('');
  const [maxValue, setMaxValue] = useState('');

  const handleResetFilters = () => {
    setSearchQuery('');
    setJurisdiction('');
    setCategory('');
    setStatus('');
    setYear('');
    setMinDate('');
    setMaxDate('');
    setMinValue('');
    setMaxValue('');
  };

  const filteredTenders = useMemo(() => {
    return initialTenders.filter((tender) => {
      // 1. Keyword Search 
      if (searchQuery) {
        const query = searchQuery.toLowerCase();
        const matchTitle = tender.title?.toLowerCase().includes(query) ?? false;
        const matchDesc = tender.description?.toLowerCase().includes(query) ?? false;
        if (!matchTitle && !matchDesc) return false;
      }
      
      // 2. Jurisdiction / Location 
      if (jurisdiction) {
        const tenderLoc = tender.location?.toLowerCase() || '';
        const selectedLoc = jurisdiction.toLowerCase();
        if (!tenderLoc.includes(selectedLoc)) return false;
      }

      // 3. Category & Status 
      if (category && tender.category?.toLowerCase() !== category.toLowerCase()) return false;
      if (status && tender.status?.toLowerCase() !== status.toLowerCase()) return false;

      // 4. Year Filter 
      if (year) {
        const targetDate = tender.closing_date || tender.publish_date;
        if (!targetDate || !targetDate.startsWith(year)) return false;
      }
      
      // 5. Value Range Filter 
      if (minValue !== '') {
        if (tender.value_amount == null || tender.value_amount < Number(minValue)) return false;
      }
      if (maxValue !== '') {
        if (tender.value_amount == null || tender.value_amount > Number(maxValue)) return false;
      }
      
      // 6. Date Range Filter 
      if (minDate) {
        if (!tender.closing_date || new Date(tender.closing_date) < new Date(minDate)) return false;
      }
      if (maxDate) {
        if (!tender.closing_date || new Date(tender.closing_date) > new Date(maxDate)) return false;
      }

      return true; 
    });
  }, [initialTenders, searchQuery, jurisdiction, category, status, year, minValue, maxValue, minDate, maxDate]);

  // Bundle and return all states and control functions
  return {
    searchQuery, setSearchQuery,
    showFilters, setShowFilters,
    jurisdiction, setJurisdiction,
    category, setCategory,
    status, setStatus,
    year, setYear,
    minDate, setMinDate,
    maxDate, setMaxDate,
    minValue, setMinValue,
    maxValue, setMaxValue,
    handleResetFilters,
    filteredTenders
  };
}