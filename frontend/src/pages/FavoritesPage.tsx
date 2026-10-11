import { useEffect, useState } from 'react';
import { Box, Container, CircularProgress, Alert, Typography } from '@mui/material';
import { getTenders, isRequestCancelled, type Tender } from '../lib/api';
import TopNav from '../components/TopNav';
import { TenderCard } from '../components/TenderCard';
import { TenderFilterPanel, TenderSearchControls } from '../components/TenderFilterBar';
import { useTenderFilters } from '../lib/useTenderFilters';
import { useFavorites } from '../lib/FavoritesContext';
import { parseSortOption } from '../lib/tenderSort';

export default function FavoritesPage() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { favorites, toggleFavorite, loadingFavorites } = useFavorites();
  const filterProps = useTenderFilters();
  const toggleExpand = (tenderId: string) =>
    setExpandedId((prev) => (prev === tenderId ? null : tenderId));

  useEffect(() => {
    if (!filterProps.filtersReady) {
      setLoading(true);
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    const { sort_by, sort_dir } = parseSortOption(filterProps.sortBy);

    getTenders({
      limit: 50,
      q: filterProps.debouncedSearchQuery || undefined,
      status: filterProps.status || undefined,
      states: filterProps.states.length ? filterProps.states : undefined,
      source_id: filterProps.sourceId || undefined,
      year: filterProps.year || undefined,
      closing_after: filterProps.minDate || undefined,
      closing_before: filterProps.maxDate || undefined,
      published_after: filterProps.publishedAfter || undefined,
      published_before: filterProps.publishedBefore || undefined,
      sort_by,
      sort_dir,
    }, controller.signal)
      .then((data) => {
        if (!cancelled) setTenders(data);
      })
      .catch((err: unknown) => {
        if (!cancelled && !isRequestCancelled(err)) {
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
    filterProps.status,
    filterProps.states,
    filterProps.sourceId,
    filterProps.year,
    filterProps.minDate,
    filterProps.maxDate,
    filterProps.publishedAfter,
    filterProps.publishedBefore,
    filterProps.sortBy,
  ]);

  const bookmarkedTenders = tenders.filter(t => favorites.has(t.tender_id));
  const isDataLoading = loading || !filterProps.filtersReady || loadingFavorites;

  return (
    <Box sx={{ flexGrow: 1, bgcolor: 'background.default', minHeight: '100vh', pb: 6, overflowAnchor: 'none' }}>
      <TopNav
        searchControls={<TenderSearchControls {...filterProps} />}
        filterPanel={<TenderFilterPanel {...filterProps} />}
      />
      <Container maxWidth="md" sx={{ pt: 2 }}>

      <Box sx={{ mt: 2, mb: 4, textAlign: 'left' }}>
          <Typography
            variant="h5"
            sx={{
              fontWeight: 800,
              color: (theme) => theme.palette.mode === 'light' ? 'primary.main' : 'secondary.main',
            }}
          >
            My favorites
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Manage and track your bookmarked tender opportunities.
          </Typography>
        </Box>
        {isDataLoading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
        )}

        {!isDataLoading && error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

        {!isDataLoading && !error && bookmarkedTenders.length === 0 && (
          <Alert severity="info" sx={{ mt: 2 }}>
            You haven't added any tenders to your favorites yet, or none match your current filters.
          </Alert>
        )}

        {!isDataLoading && !error && bookmarkedTenders.map((tender) => (
          <TenderCard
            key={tender.tender_id}
            tender={tender}
            isFavorite={true}
            onToggleFavorite={toggleFavorite}
            expanded={expandedId === tender.tender_id}
            onToggleExpand={toggleExpand}
          />
        ))}

      </Container>
    </Box>
  );
}
