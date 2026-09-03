import { useEffect, useState } from 'react';
import {
  Typography,
  Button,
  Container,
  Card,
  CardContent,
  CardActions,
  Box,
  Chip,
  Link,
  CircularProgress,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  IconButton,
  Divider,
  TextField,
  Collapse,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  InputAdornment
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import SearchIcon from '@mui/icons-material/Search';
import FilterListIcon from '@mui/icons-material/FilterList';
import { getTenders, type Tender } from '../lib/api';
import TopNav from '../components/TopNav';

function formatDate(value: string | null): string {
  if (!value) return 'Not specified';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString('en-AU', { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatMoney(tender: Tender): string {
  if (tender.value_amount != null) {
    const currency = tender.value_currency ?? 'AUD';
    try {
      return new Intl.NumberFormat('en-AU', { style: 'currency', currency }).format(tender.value_amount);
    } catch {
      return `${tender.value_amount} ${currency}`;
    }
  }
  if (tender.value_notes) return tender.value_notes;
  return 'Not disclosed';
}

function isRecentlySeen(tender: Tender): boolean {
  if (!tender.first_seen_at) return false;
  const ageDays = (Date.now() - new Date(tender.first_seen_at).getTime()) / 86_400_000;
  return ageDays <= 7;
}

function TenderCard({
  tender,
  isFavorite,
  onToggleFavorite,
  onOpenDetails,
}: {
  tender: Tender;
  isFavorite: boolean;
  onToggleFavorite: (tenderId: string) => void;
  onOpenDetails: (tender: Tender) => void;
}) {
  return (
    <Card sx={{ mb: 2, border: '1px solid #e0e0e0', borderRadius: 2, boxShadow: '0 2px 4px rgba(0,0,0,0.04)' }}>
      <CardContent sx={{ pb: 1 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1.5 }}>
          <Typography variant="h6" component="div" sx={{ textAlign: 'left', fontWeight: 600, fontSize: '1.1rem' }}>
            {tender.title || 'Untitled Tender'}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, flexShrink: 0, ml: 1 }}>
            {isRecentlySeen(tender) && <Chip label="NEW" color="primary" size="small" />}
            <Chip
              label="FAVORITE"
              color={isFavorite ? 'warning' : 'default'}
              size="small"
              onClick={() => onToggleFavorite(tender.tender_id)}
              sx={{ cursor: 'pointer' }}
            />
          </Box>
        </Box>

        <Box
          sx={{
            p: 1.5,
            mb: 2,
            bgcolor: '#f4f7fb',
            borderRadius: 1.5,
            borderLeft: '4px solid #1976d2',
            textAlign: 'left',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
            <AutoAwesomeIcon sx={{ fontSize: 16, color: '#1976d2' }} />
            <Typography variant="caption" sx={{ fontWeight: 700, color: '#1976d2', textTransform: 'uppercase' }}>
              AI Summary
            </Typography>
          </Box>
          <Typography
            variant="body2"
            sx={{
              color: '#333',
              fontSize: '0.875rem',
              lineHeight: 1.4,
              display: '-webkit-box',
              WebkitLineClamp: 3,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {tender.description
              ? tender.description.slice(0, 180) + '…'
              : 'No AI summary generated for this tender yet.'}
          </Typography>
        </Box>

        <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
          <strong>ATM ID:</strong> {tender.source_reference_id ?? 'Not specified'}
        </Typography>
        <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
          <strong>Closing Date:</strong> {formatDate(tender.closing_date)}
        </Typography>
        <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
          <strong>Agency:</strong> {tender.issuing_agency ?? 'Not specified'}
        </Typography>
        <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
          <strong>Source:</strong>{' '}
          {tender.source_url ? (
            <Link href={tender.source_url} target="_blank" rel="noopener noreferrer">
              {tender.source_url}
            </Link>
          ) : (
            'Not specified'
          )}
        </Typography>
      </CardContent>

      <CardActions sx={{ justifyContent: 'center', pt: 0, pb: 1.5 }}>
        <Button size="small" variant="text" onClick={() => onOpenDetails(tender)}>
          View More
        </Button>
      </CardActions>
    </Card>
  );
}

function TenderDetailModal({
  tender,
  open,
  onClose,
}: {
  tender: Tender | null;
  open: boolean;
  onClose: () => void;
}) {
  if (!tender) return null;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      scroll="paper"
      slotProps={{
        backdrop: {
          sx: { backgroundColor: 'rgba(0, 0, 0, 0.55)' },
        },
      }}
    >
      <DialogTitle sx={{ m: 0, p: 2.5, pr: 6, fontWeight: 600 }}>
        {tender.title || 'Tender Details'}
        <IconButton
          aria-label="close"
          onClick={onClose}
          sx={{
            position: 'absolute',
            right: 12,
            top: 12,
            color: (theme) => theme.palette.grey[500],
          }}
        >
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <Divider />

      <DialogContent dividers sx={{ p: 3, textAlign: 'left' }}>
        <Box sx={{ p: 2, mb: 3, bgcolor: '#f4f7fb', borderRadius: 1.5, borderLeft: '4px solid #1976d2' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
            <AutoAwesomeIcon sx={{ fontSize: 18, color: '#1976d2' }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 700, color: '#1976d2' }}>
              AI Summary & Insights
            </Typography>
          </Box>
          <Typography variant="body2" sx={{ color: '#444', lineHeight: 1.6 }}>
            {tender.description
              ? tender.description.slice(0, 300) + '…'
              : 'Summary placeholder: Full key requirements, timeline, and scope summary will appear here once the AI enrichment pipeline is run.'}
          </Typography>
        </Box>

        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2, mb: 3 }}>
          <Typography variant="body2">
            <strong>ATM ID:</strong> {tender.source_reference_id ?? 'Not specified'}
          </Typography>
          <Typography variant="body2">
            <strong>Monetary Value:</strong> {formatMoney(tender)}
          </Typography>
          <Typography variant="body2">
            <strong>Opening Date:</strong> {formatDate(tender.publish_date)}
          </Typography>
          <Typography variant="body2">
            <strong>Closing Date:</strong> {formatDate(tender.closing_date)}
          </Typography>
          <Typography variant="body2">
            <strong>Agency:</strong> {tender.issuing_agency ?? 'Not specified'}
          </Typography>
          <Typography variant="body2">
            <strong>Location:</strong> {tender.location ?? 'Not specified'}
          </Typography>
          <Typography variant="body2">
            <strong>Category:</strong> {tender.category ?? 'Not specified'}
          </Typography>
          <Typography variant="body2">
            <strong>Status:</strong> {tender.status ?? 'Active'}
          </Typography>
        </Box>

        <Divider sx={{ my: 2 }} />

        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
          Full Tender Description
        </Typography>
        <Box
          sx={{
            p: 2,
            bgcolor: '#fafafa',
            border: '1px solid #e0e0e0',
            borderRadius: 1,
            maxHeight: '300px',
            overflowY: 'auto',
            whiteSpace: 'pre-wrap',
            fontSize: '0.875rem',
            lineHeight: 1.6,
          }}
        >
          {tender.description ?? 'No description extracted for this tender.'}
        </Box>

        {tender.source_url && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="body2">
              <strong>Original Portal Link:</strong>{' '}
              <Link href={tender.source_url} target="_blank" rel="noopener noreferrer">
                {tender.source_url}
              </Link>
            </Typography>
          </Box>
        )}
      </DialogContent>

      <DialogActions sx={{ p: 2 }}>
        <Button onClick={onClose} variant="contained" color="primary">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default function TendersPage() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [favorites, setFavorites] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedTender, setSelectedTender] = useState<Tender | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

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

  useEffect(() => {
    let cancelled = false;

    setLoading(true);
    setError(null);
    getTenders({ limit: 50 })
      .then((data) => {
        if (!cancelled) setTenders(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Something went wrong loading tenders.');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const handleToggleFavorite = (tenderId: string) => {
    setFavorites((prev) => {
      const next = new Set(prev);
      if (next.has(tenderId)) {
        next.delete(tenderId);
      } else {
        next.add(tenderId);
      }
      return next;
    });
  };

  const handleOpenDetails = (tender: Tender) => {
    setSelectedTender(tender);
    setModalOpen(true);
  };

  const handleCloseDetails = () => {
    setModalOpen(false);
  };

  // === CLIENT-SIDE FILTERING LOGIC ===
  const filteredTenders = tenders.filter((tender) => {
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

  // Apply sorting on the FILTERED list, not the raw tenders list
  const sortedTenders = [...filteredTenders].sort(
    (a, b) => Number(favorites.has(b.tender_id)) - Number(favorites.has(a.tender_id))
  );

  return (
    <Box sx={{ flexGrow: 1, bgcolor: '#fcfcfc', minHeight: '100vh', pb: 6 }}>
      <TopNav />
      <Container maxWidth="md">
        
        {/* === FILTER & SEARCH SECTION === */}
        <Box sx={{ mb: 4, p: 2, bgcolor: '#ffffff', borderRadius: 2, border: '1px solid #e0e0e0' }}>
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
            <TextField
              fullWidth
              variant="outlined"
              size="small"
              placeholder="Search tenders by keyword..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              slotProps={{
                input: {
                  startAdornment: (
                    <InputAdornment position="start">
                      <SearchIcon color="action" />
                    </InputAdornment>
                  ),
                }
              }}
            />
            <Button 
              variant={showFilters ? "contained" : "outlined"} 
              startIcon={<FilterListIcon />}
              onClick={() => setShowFilters(!showFilters)}
              sx={{ flexShrink: 0 }}
            >
              Filters
            </Button>
          </Box>

          <Collapse in={showFilters}>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 2, pt: 2, borderTop: '1px dashed #ccc' }}>
              
              <Box sx={{ display: 'flex', gap: 2 }}>
                <FormControl size="small" fullWidth>
                  <InputLabel>Jurisdiction</InputLabel>
                  <Select value={jurisdiction} label="Jurisdiction" onChange={(e) => setJurisdiction(e.target.value)}>
                    <MenuItem value=""><em>All</em></MenuItem>
                    <MenuItem value="Western Australia">Western Australia (WA)</MenuItem>
                    <MenuItem value="Victoria">Victoria (VIC)</MenuItem>
                    <MenuItem value="New South Wales">New South Wales (NSW)</MenuItem>
                  </Select>
                </FormControl>

                <FormControl size="small" fullWidth>
                  <InputLabel>Year</InputLabel>
                  <Select value={year} label="Year" onChange={(e) => setYear(e.target.value)}>
                    <MenuItem value=""><em>All Time</em></MenuItem>
                    <MenuItem value="2026">2026</MenuItem>
                    <MenuItem value="2025">2025</MenuItem>
                    <MenuItem value="2024">2024</MenuItem>
                  </Select>
                </FormControl>

                <FormControl size="small" fullWidth>
                  <InputLabel>Category</InputLabel>
                  <Select value={category} label="Category" onChange={(e) => setCategory(e.target.value)}>
                    <MenuItem value=""><em>All</em></MenuItem>
                    <MenuItem value="tender">Tender</MenuItem>
                    <MenuItem value="rfq">RFQ</MenuItem>
                    <MenuItem value="eoi">EOI</MenuItem>
                    <MenuItem value="grant">Grant</MenuItem>
                  </Select>
                </FormControl>

                <FormControl size="small" fullWidth>
                  <InputLabel>Status</InputLabel>
                  <Select value={status} label="Status" onChange={(e) => setStatus(e.target.value)}>
                    <MenuItem value=""><em>All</em></MenuItem>
                    <MenuItem value="open">Open</MenuItem>
                    <MenuItem value="closed">Closed</MenuItem>
                    <MenuItem value="awarded">Awarded</MenuItem>
                    <MenuItem value="unknown">Unknown</MenuItem>
                  </Select>
                </FormControl>

                <FormControl size="small" fullWidth disabled>
                  <InputLabel>Tags (Coming soon)</InputLabel>
                  <Select value="" label="Tags (Coming soon)">
                    <MenuItem value=""><em>Pending AI</em></MenuItem>
                  </Select>
                </FormControl>
              </Box>

              <Box sx={{ display: 'flex', gap: 2 }}>
                <TextField
                  size="small"
                  fullWidth
                  type="date"
                  label="Closing After"
                  slotProps={{ inputLabel: { shrink: true } }}
                  value={minDate}
                  onChange={(e) => setMinDate(e.target.value)}
                />
                <TextField
                  size="small"
                  fullWidth
                  type="date"
                  label="Closing Before"
                  slotProps={{ inputLabel: { shrink: true } }}
                  value={maxDate}
                  onChange={(e) => setMaxDate(e.target.value)}
                />
                <TextField
                  size="small"
                  fullWidth
                  type="number"
                  label="Min Value ($)"
                  value={minValue}
                  onChange={(e) => setMinValue(e.target.value)}
                />
                <TextField
                  size="small"
                  fullWidth
                  type="number"
                  label="Max Value ($)"
                  value={maxValue}
                  onChange={(e) => setMaxValue(e.target.value)}
                />
              </Box>

              <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 1 }}>
                <Button size="small" color="inherit" onClick={handleResetFilters}>
                  Clear All Filters
                </Button>
              </Box>

            </Box>
          </Collapse>
        </Box>
        {/* === END FILTER & SEARCH SECTION === */}

        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
            <CircularProgress />
          </Box>
        )}

        {!loading && error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {!loading && !error && sortedTenders.length === 0 && (
          <Alert severity="info">
            No tenders match your current filters.
          </Alert>
        )}

        {!loading &&
          !error &&
          sortedTenders.map((tender) => (
            <TenderCard
              key={tender.tender_id}
              tender={tender}
              isFavorite={favorites.has(tender.tender_id)}
              onToggleFavorite={handleToggleFavorite}
              onOpenDetails={handleOpenDetails}
            />
          ))}

        <TenderDetailModal tender={selectedTender} open={modalOpen} onClose={handleCloseDetails} />
      </Container>
    </Box>
  );
}