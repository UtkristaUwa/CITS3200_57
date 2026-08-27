import { useEffect, useState } from 'react';
import {
  AppBar, Toolbar, Typography, Button, Container, Card, CardContent,
  CardActions, Collapse, Box, Chip, Link, CircularProgress, Alert
} from '@mui/material';
import { getTenders, type Tender } from './lib/api';

const DESCRIPTION_PREVIEW_LENGTH = 160;

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
      // Unrecognised currency code — fall back to a plain number rather than throwing.
      return `${tender.value_amount} ${currency}`;
    }
  }
  if (tender.value_notes) return tender.value_notes;
  return 'Not disclosed';
}

// Simple client-side "new" heuristic — there's no is_new field in the
// schema. Anything upsert_tender() first saw within the last 7 days counts.
function isRecentlySeen(tender: Tender): boolean {
  const ageDays = (Date.now() - new Date(tender.first_seen_at).getTime()) / 86_400_000;
  return ageDays <= 7;
}

// Single Tender Card Component
function TenderCard({
  tender,
  isFavorite,
  onToggleFavorite,
}: {
  tender: Tender;
  isFavorite: boolean;
  onToggleFavorite: (tenderId: string) => void;
}) {
  // State to control the expand/collapse action
  const [expanded, setExpanded] = useState(false);

  const descriptionPreview = tender.description
    ? tender.description.length > DESCRIPTION_PREVIEW_LENGTH
      ? `${tender.description.slice(0, DESCRIPTION_PREVIEW_LENGTH)}…`
      : tender.description
    : 'Not specified';

  return (
    <Card sx={{ mb: 2, border: '1px solid #ccc', boxShadow: 'none' }}>
      <CardContent>
        {/* ==========================================
            1. Collapsed State (Default Display)
            Contains Title, ATM ID, Closing Date, Agency, Description
            ========================================== */}

        {/* Title and Badges */}
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
          <Typography variant="h6" component="div" sx={{ textAlign: 'left' }}>
            {tender.title}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1 }}>
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

        {/* Outer layer information (Left Aligned, Reordered) */}
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
          <strong>Description:</strong> {descriptionPreview}
        </Typography>
        <Typography variant="body2" sx={{ textAlign: 'left', mb: 1.5 }}>
          <strong>Source:</strong>{' '}
          {tender.source_url ? (
            <Link href={tender.source_url} target="_blank" rel="noopener noreferrer">
              {tender.source_url}
            </Link>
          ) : (
            'Not specified'
          )}
        </Typography>

        {/* ==========================================
            2. Expanded State (After clicking View More)
            Contains Summary, Opening Date, Closing Date, Monetary, ATM ID, Location, Agency, Description
            ========================================== */}
        <Collapse in={expanded} timeout="auto" unmountOnExit>
          <Box sx={{ mt: 2, p: 2, bgcolor: '#f9f9f9', borderRadius: 1, textAlign: 'left' }}>

            <Typography variant="body2" component="p" sx={{ mb: 2, color: '#888' }}>
              {/* tender_enrichment isn't wired up yet — this stays a placeholder
                  until the AI summarisation endpoint exists. Not a bug. */}
              <strong>Summary:</strong> Not generated yet.
            </Typography>

            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Opening Date:</strong> {formatDate(tender.publish_date)}
            </Typography>

            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Closing Date:</strong> {formatDate(tender.closing_date)}
            </Typography>

            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Monetary:</strong> {formatMoney(tender)}
            </Typography>

            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>ATM ID:</strong> {tender.source_reference_id ?? 'Not specified'}
            </Typography>

            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Location:</strong> {tender.location ?? 'Not specified'}
            </Typography>

            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Agency:</strong> {tender.issuing_agency ?? 'Not specified'}
            </Typography>

            {/* Description at the bottom, full text this time */}
            <Typography variant="body2" sx={{ mt: 2 }}>
              <strong>Description:</strong>
            </Typography>
            <Box
              sx={{
                mt: 1,
                p: 2,
                border: '1px dashed #ccc',
                borderRadius: 1,
                minHeight: '80px',
                whiteSpace: 'pre-wrap',
              }}
            >
              {tender.description ?? 'No description extracted for this tender.'}
            </Box>
          </Box>
        </DialogContent>
        
        {/* Close Button at the bottom of the modal */}
        <DialogActions>
          <Button onClick={handleClose} color="primary">
            Close
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

// Main Page Component
export default function BasicSkeletonApp() {
  const [tenders, setTenders] = useState<Tender[]>([]);
  // Starring isn't wired to the backend yet — user_tender_status needs an
  // authenticated endpoint before this can persist. Kept local-only for now,
  // tracked by tender_id so it survives a re-render without depending on
  // array order.
  const [favorites, setFavorites] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  const sortedTenders = [...tenders].sort(
    (a, b) => Number(favorites.has(b.tender_id)) - Number(favorites.has(a.tender_id))
  );

  return (
    <Box sx={{ flexGrow: 1 }}>
      {/* Top Navigation Bar */}
      <AppBar position="static" color="default" sx={{ mb: 3 }}>
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1, textAlign: 'left' }}>
            TenderAI
          </Typography>
          <Button color="inherit">Feed</Button>
          <Button color="inherit">Starred</Button>
          <Button color="inherit">Admin</Button>
        </Toolbar>
      </AppBar>

      {/* Main Content */}
      <Container maxWidth="md">
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
            No tenders yet — nothing's been submitted to BigQuery. Run{' '}
            <code>ingestion/validate_and_submit.py</code> against some real data first.
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
            />
          ))}
      </Container>
    </Box>
  );
}
