import { useEffect, useState } from 'react';
import { Box, Container, CircularProgress, Alert } from '@mui/material';
import { getTenders, type Tender } from '../lib/api';
import TopNav from '../components/TopNav';
import { TenderCard, TenderDetailModal } from '../components/TenderCard';
import TenderFilterBar from '../components/TenderFilterBar';
import { useTenderFilters } from '../lib/useTenderFilters';
import { useFavorites } from '../lib/FavoritesContext';

export default function TendersPage() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedTender, setSelectedTender] = useState<Tender | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const { favorites, toggleFavorite } = useFavorites();
  
  const filterProps = useTenderFilters();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    
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

  const sortedTenders = [...tenders].sort(
    (a, b) => Number(favorites.has(b.tender_id)) - Number(favorites.has(a.tender_id))
  );

  return (
    <Box sx={{ flexGrow: 1, bgcolor: 'background.default', minHeight: '100vh', pb: 6 }}>
      <TopNav />
      <Container maxWidth="md">
        
        <TenderFilterBar {...filterProps} />

        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
        )}

        {!loading && error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

        {!loading && !error && sortedTenders.length === 0 && (
          <Alert severity="info">No tenders match your current filters.</Alert>
        )}

        {!loading && !error && sortedTenders.map((tender) => (
          <TenderCard
            key={tender.tender_id}
            tender={tender}
            isFavorite={favorites.has(tender.tender_id)}
            onToggleFavorite={toggleFavorite}
            onOpenDetails={(t) => { setSelectedTender(t); setModalOpen(true); }}
          />
        ))}

        <TenderDetailModal tender={selectedTender} open={modalOpen} onClose={() => setModalOpen(false)} />
      </Container>
    </Box>
  );
}