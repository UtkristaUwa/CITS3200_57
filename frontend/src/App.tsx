import { useEffect, useState } from 'react';
import {
  AppBar,
  Toolbar,
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
import { getTenders, type Tender } from './lib/api';

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

// Single Tender Card Component
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
        {/* Title and Badges */}
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

        {/* AI Summary Box */}
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

        {/* Key Metadata Fields */}
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

// Tender Detail Modal Component
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
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth scroll="paper" slotProps={{ backdrop: { sx: { backgroundColor: 'rgba(0, 0, 0, 0.55)' } } }}>
      <DialogTitle sx={{ m: 0, p: 2.5, pr: 6, fontWeight: 600 }}>
        {tender.title || 'Tender Details'}
        <IconButton aria-label="close" onClick={onClose} sx={{ position: 'absolute', right: 12, top: 12, color: (theme) => theme.palette.grey[500] }}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <Divider />
      <DialogContent dividers sx={{ p: 3, textAlign: 'left' }}>
        <Box sx={{ p: 2, mb: 3, bgcolor: '#f4f7fb', borderRadius: 1.5, borderLeft: '4px solid #1976d2' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
            <AutoAwesomeIcon sx={{ fontSize: 18, color: '#1976d2' }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 700, color: '#1976d2' }}>AI Summary & Insights</Typography>
          </Box>
          <Typography variant="body2" sx={{ color: '#444', lineHeight: 1.6 }}>
            {tender.description ? tender.description.slice(0, 300) + '…' : 'Summary placeholder.'}
          </Typography>
        </Box>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2, mb: 3 }}>
          <Typography variant="body2"><strong>ATM ID:</strong> {tender.source_reference_id ?? 'Not specified'}</Typography>
          <Typography variant="body2"><strong>Monetary Value:</strong> {formatMoney(tender)}</Typography>
          <Typography variant="body2"><strong>Opening Date:</strong> {formatDate(tender.publish_date)}</Typography>
          <Typography variant="body2"><strong>Closing Date:</strong> {formatDate(tender.closing_date)}</Typography>
          <Typography variant="body2"><strong>Agency:</strong> {tender.issuing_agency ?? 'Not specified'}</Typography>
          <Typography variant="body2"><strong>Location:</strong> {tender.location ?? 'Not specified'}</Typography>
          <Typography variant="body2"><strong>Category:</strong> {tender.category ?? 'Not specified'}</Typography>
          <Typography variant="body2"><strong>Status:</strong> {tender.status ?? 'Active'}</Typography>
        </Box>
        <Divider sx={{ my: 2 }} />
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Full Tender Description</Typography>
        <Box sx={{ p: 2, bgcolor: '#fafafa', border: '1px solid #e0e0e0', borderRadius: 1, maxHeight: '300px', overflowY: 'auto', whiteSpace: 'pre-wrap', fontSize: '0.875rem', lineHeight: 1.6 }}>
          {tender.description ?? 'No description extracted for this tender.'}
        </Box>
        {tender.source_url && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="body2">
              <strong>Original Portal Link:</strong> <Link href={tender.source_url} target="_blank" rel="noopener noreferrer">{tender.source_url}</Link>
            </Typography>
          </Box>
        )}
      </DialogContent>
      <DialogActions sx={{ p: 2 }}>
        <Button onClick={onClose} variant="contained" color="primary">Close</Button>
      </DialogActions>
    </Dialog>
  );
}

// Main App Component
export default function BasicSkeletonApp() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [favorites, setFavorites] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modal State
  const [selectedTender, setSelectedTender] = useState<Tender | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  // --- UI State for Search & Filters ---
  const [searchQuery, setSearchQuery] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  
  // Static filter selections
  const [jurisdiction, setJurisdiction] = useState('');
  const [year, setYear] = useState('');
  const [tag, setTag] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getTenders({ limit: 50 })
      .then((data) => {
        if (!cancelled) setTenders(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Something went wrong loading tenders.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const handleToggleFavorite = (tenderId: string) => {
    setFavorites((prev) => {
      const next = new Set(prev);
      if (next.has(tenderId)) next.delete(tenderId);
      else next.add(tenderId);
      return next;
    });
  };

  const handleOpenDetails = (tender: Tender) => {
    setSelectedTender(tender);
    setModalOpen(true);
  };

  const handleCloseDetails = () => setModalOpen(false);

  // Apply basic sorting (favorites at top)
  const sortedTenders = [...tenders].sort(
    (a, b) => Number(favorites.has(b.tender_id)) - Number(favorites.has(a.tender_id))
  );

  return (
    <Box sx={{ flexGrow: 1, bgcolor: '#fcfcfc', minHeight: '100vh', pb: 6 }}>
      {/* Top Navigation Bar */}
      <AppBar position="static" color="default" sx={{ mb: 3 }}>
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1, textAlign: 'left', fontWeight: 700 }}>
            TenderAI
          </Typography>
          <Button color="inherit">Feed</Button>
          <Button color="inherit">Starred</Button>
          <Button color="inherit">Admin</Button>
        </Toolbar>
      </AppBar>

      {/* Main Content */}
      <Container maxWidth="md">
        
        {/* === FILTER & SEARCH SECTION === */}
        <Box sx={{ mb: 4, p: 2, bgcolor: '#ffffff', borderRadius: 2, border: '1px solid #e0e0e0' }}>
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
            {/* Main Text Search Bar */}
            <TextField
              fullWidth
              variant="outlined"
              size="small"
              placeholder="Search tenders by keyword..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon color="action" />
                  </InputAdornment>
                ),
              }}
            />
            {/* Toggle Button for Advanced Filters */}
            <Button 
              variant={showFilters ? "contained" : "outlined"} 
              startIcon={<FilterListIcon />}
              onClick={() => setShowFilters(!showFilters)}
              sx={{ flexShrink: 0 }}
            >
              Filters
            </Button>
          </Box>

          {/* Expandable Advanced Filters Area */}
          <Collapse in={showFilters}>
            <Box sx={{ display: 'flex', gap: 2, mt: 2, pt: 2, borderTop: '1px dashed #ccc' }}>
              
              <FormControl size="small" fullWidth>
                <InputLabel>Jurisdiction</InputLabel>
                <Select
                  value={jurisdiction}
                  label="Jurisdiction"
                  onChange={(e) => setJurisdiction(e.target.value)}
                >
                  <MenuItem value=""><em>All</em></MenuItem>
                  <MenuItem value="WA">Western Australia (WA)</MenuItem>
                  <MenuItem value="QLD">Queensland (QLD)</MenuItem>
                  <MenuItem value="NSW">New South Wales (NSW)</MenuItem>
                </Select>
              </FormControl>

              <FormControl size="small" fullWidth>
                <InputLabel>Year</InputLabel>
                <Select
                  value={year}
                  label="Year"
                  onChange={(e) => setYear(e.target.value)}
                >
                  <MenuItem value=""><em>All</em></MenuItem>
                  <MenuItem value="2026">2026</MenuItem>
                  <MenuItem value="2027">2027</MenuItem>
                </Select>
              </FormControl>

              <FormControl size="small" fullWidth>
                <InputLabel>Tags</InputLabel>
                <Select
                  value={tag}
                  label="Tags"
                  onChange={(e) => setTag(e.target.value)}
                >
                  <MenuItem value=""><em>All Topics</em></MenuItem>
                  <MenuItem value="homelessness">Homelessness</MenuItem>
                  <MenuItem value="education">Education</MenuItem>
                  <MenuItem value="finance">Finance</MenuItem>
                </Select>
              </FormControl>

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
          <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>
        )}

        {!loading && !error && sortedTenders.length === 0 && (
          <Alert severity="info">
            No tenders yet — nothing's been submitted to BigQuery.
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