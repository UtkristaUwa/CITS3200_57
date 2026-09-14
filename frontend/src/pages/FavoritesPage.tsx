import { useEffect, useState } from 'react';
import { Box, Container, CircularProgress, Alert, Typography } from '@mui/material';
import { getTenders, type Tender } from '../lib/api';
import TopNav from '../components/TopNav';
import { TenderCard, TenderDetailModal } from '../components/TenderCard';
import TenderFilterBar from '../components/TenderFilterBar';
import { useTenderFilters } from '../lib/useTenderFilters';
import { useFavorites } from '../lib/FavoritesContext';

export default function FavoritesPage() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedTender, setSelectedTender] = useState<Tender | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const { favorites, toggleFavorite, loadingFavorites } = useFavorites();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getTenders({ limit: 50 })
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
  }, []);

  // Core difference: filter to keep only favorited items first, then pass to the filter hook
  const bookmarkedTenders = tenders.filter(t => favorites.has(t.tender_id));
  const filterProps = useTenderFilters(bookmarkedTenders);

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
            You haven't added any tenders to your favorites yet. Go to the Home page to discover opportunities.
          </Alert>
        )}

        {!isDataLoading && !error && bookmarkedTenders.length > 0 && filterProps.filteredTenders.length === 0 && (
          <Alert severity="info">
            No favorite tenders match your current filters.
          </Alert>
        )}

        {!isDataLoading && !error && filterProps.filteredTenders.map((tender) => (
          <TenderCard
            key={tender.tender_id}
            tender={tender}
            isFavorite={true}
            onToggleFavorite={toggleFavorite}
            onOpenDetails={(t) => { setSelectedTender(t); setModalOpen(true); }}
          />
        ))}

        <TenderDetailModal tender={selectedTender} open={modalOpen} onClose={() => setModalOpen(false)} />
      </Container>
    </Box>
  );
}