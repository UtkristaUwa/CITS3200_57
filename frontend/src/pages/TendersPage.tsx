import { useEffect, useState } from 'react';
import { Box, Container, CircularProgress, Alert, Button } from '@mui/material';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import { getTenders, isRequestCancelled, type Tender } from '../lib/api';
import TopNav from '../components/TopNav';
import { TenderCard } from '../components/TenderCard';
import { TenderFilterPanel, TenderSearchControls } from '../components/TenderFilterBar';
import { useTenderFilters } from '../lib/useTenderFilters';
import { useFavorites } from '../lib/FavoritesContext';
import { useAuth } from '../lib/AuthContext';
import { useDisplaySettings } from '../lib/DisplaySettingsContext';

import { parseSortOption } from '../lib/tenderSort';

function isTenderNew(
  tender: Tender,
  previousTenderVisit: number | null,
  firstTenderVisit: boolean,
  tenderVisitReady: boolean,
): boolean {
  if (!tenderVisitReady || firstTenderVisit || previousTenderVisit === null || !tender.first_seen_at) {
    return false;
  }

  const firstSeenAt = new Date(tender.first_seen_at).getTime();
  return Number.isFinite(firstSeenAt) && firstSeenAt > previousTenderVisit;
}

export default function TendersPage() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { favorites, toggleFavorite } = useFavorites();
  const { previousTenderVisit, firstTenderVisit, tenderVisitReady } = useAuth();
  const { settings: displaySettings } = useDisplaySettings();
  const toggleExpand = (tenderId: string) =>
    setExpandedId((prev) => (prev === tenderId ? null : tenderId));
  
  const filterProps = useTenderFilters();

  useEffect(() => {
    if (!filterProps.filtersReady) {
      setLoading(true);
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setWarning(null);
    
    const mode = filterProps.advancedSearch ? 'semantic' : 'keyword';
    const { sort_by, sort_dir } = parseSortOption(filterProps.sortBy);

    const baseParams = {
      limit: 50,
      q: filterProps.debouncedSearchQuery || undefined,
      status: filterProps.status || undefined,
      states: filterProps.states.length ? filterProps.states : undefined,
      focus_areas: filterProps.focusAreas.length ? filterProps.focusAreas : undefined,
      source_id: filterProps.sourceId || undefined,
      year: filterProps.year || undefined,
      closing_after: filterProps.minDate || undefined,
      closing_before: filterProps.maxDate || undefined,
      published_after: filterProps.publishedAfter || undefined,
      published_before: filterProps.publishedBefore || undefined,
      sort_by,
      sort_dir,
      include_irrelevant: displaySettings.show_irrelevant,
    };

    getTenders({ 
      ...baseParams,
      mode,
    }, controller.signal)
      .then((data) => {
        if (!cancelled) setTenders(data);
      })
      .catch(async (err: unknown) => {
        if (cancelled || isRequestCancelled(err)) return;
        if (mode === 'semantic') {
          // AI search failed — notify user and fallback to fast keyword search
          setWarning('AI Semantic Search encountered an issue. Displaying fast keyword search results instead.');
          try {
            const fallbackData = await getTenders({
              ...baseParams,
              mode: 'keyword',
            }, controller.signal);
            if (!cancelled) setTenders(fallbackData);
          } catch (fallbackErr: unknown) {
            if (!cancelled && !isRequestCancelled(fallbackErr)) {
              setWarning(null);
              setError(fallbackErr instanceof Error ? fallbackErr.message : 'Failed to load tenders.');
            }
          }
        } else {
          setError(err instanceof Error ? err.message : 'Failed to load tenders.');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
      
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [
    filterProps.filtersReady,
    filterProps.debouncedSearchQuery,
    filterProps.advancedSearch,
    filterProps.sortBy,
    filterProps.status,
    filterProps.states,
    filterProps.focusAreas,
    filterProps.sourceId,
    filterProps.year,
    filterProps.minDate,
    filterProps.maxDate,
    filterProps.publishedAfter,
    filterProps.publishedBefore,
    // The toggle changes the query the API runs, so flipping it has to refetch
    // rather than just re-render what is already loaded.
    displaySettings.show_irrelevant,
  ]);

  // Strict sort: preserve server-ordered tenders without pinning favorites to the top
  const sortedTenders = tenders;
  const isDataLoading = loading || !filterProps.filtersReady || !tenderVisitReady;

  return (
    <Box sx={{ flexGrow: 1, bgcolor: 'background.default', minHeight: '100vh', pb: 6, overflowAnchor: 'none' }}>
      <TopNav
        searchControls={<TenderSearchControls {...filterProps} />}
        filterPanel={<TenderFilterPanel {...filterProps} />}
      />
      <Container maxWidth="md" sx={{ pt: 3 }}>

        {isDataLoading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
        )}

        {!isDataLoading && warning && <Alert severity="warning" sx={{ mb: 2 }}>{warning}</Alert>}
        {!isDataLoading && error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

        {!isDataLoading && !error && sortedTenders.length === 0 && (
          <Alert
            severity="info"
            action={
              filterProps.debouncedSearchQuery && !filterProps.advancedSearch ? (
                <Button
                  color="inherit"
                  size="small"
                  startIcon={<AutoAwesomeIcon />}
                  onClick={() => filterProps.setAdvancedSearch(true)}
                  sx={{ textTransform: 'none' }}
                >
                  Try Advanced
                </Button>
              ) : undefined
            }
          >
            {filterProps.debouncedSearchQuery && !filterProps.advancedSearch
              ? `No exact keyword matches found for "${filterProps.debouncedSearchQuery}". Try Advanced Search for AI conceptual matching.`
              : 'No tenders match your current filters.'}
          </Alert>
        )}

        {!isDataLoading && !error && sortedTenders.map((tender) => (
          <TenderCard
            key={tender.tender_id}
            tender={tender}
            isNew={isTenderNew(tender, previousTenderVisit, firstTenderVisit, tenderVisitReady)}
            isFavorite={favorites.has(tender.tender_id)}
            onToggleFavorite={toggleFavorite}
            expanded={expandedId === tender.tender_id}
            onToggleExpand={toggleExpand}
          />
        ))}

      </Container>
    </Box>
  );
}
