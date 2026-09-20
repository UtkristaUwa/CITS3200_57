import { useEffect, useState } from 'react';
import { Box, Container, CircularProgress, Alert, Typography } from '@mui/material';
import { getTenders, type Tender } from '../lib/api';
import TopNav from '../components/TopNav';
import { TenderCard } from '../components/TenderCard';
import TenderFilterBar from '../components/TenderFilterBar';
import { useTenderFilters } from '../lib/useTenderFilters';
import { useFavorites } from '../lib/FavoritesContext';

export default function FavoritesPage() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const toggleExpanded = (tenderId: string) =>
    setExpandedId((prev) => (prev === tenderId ? null : tenderId));

  const { favorites, toggleFavorite, loadingFavorites } = useFavorites();
  const filterProps = useTenderFilters();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setExpandedId(null);
    getTenders({
      limit: 50,
      q: filterProps.searchQuery || undefined,
      status: filterProps.status || undefined,
      category: filterProps.category || undefined,
      location: filterProps.jurisdiction || undefined,
      year: filterProps.year || undefined,
      min_value: filterProps.minValue ? Number(filterProps.minValue) : undefined,
      max_value: filterProps.maxValue ? Number(filterProps.maxValue) : undefined,
      closing_after: filterProps.minDate || undefined,
      closing_before: filterProps.maxDate || undefined,
    })
      .then((data) => {
        if (!cancelled) setTenders(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load tenders.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [
    filterProps.searchQuery, filterProps.status, filterProps.category, 
    filterProps.jurisdiction, filterProps.year, filterProps.minValue, 
    filterProps.maxValue, filterProps.minDate, filterProps.maxDate
  ]);

  const bookmarkedTenders = tenders.filter(t => favorites.has(t.tender_id));
  const isDataLoading = loading || loadingFavorites;

  return (
    <Box sx={{ flexGrow: 1, bgcolor: '#fcfcfc', minHeight: '100vh', pb: 6 }}>
      <TopNav />
      <Container maxWidth="md">

      <Box sx={{ mt: 2, mb: 4, textAlign: 'left' }}>
          <Typography variant="h5" sx={{ fontWeight: 800, color: '#1a1a1a' }}>
            My Favorites
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Manage and track your bookmarked tender opportunities.
          </Typography>
        </Box>
        
        <TenderFilterBar {...filterProps} />

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
            onToggleExpand={toggleExpanded}
          />
        ))}

      </Container>
    </Box>
  );
}