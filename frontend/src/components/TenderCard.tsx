import {
    Card,
    CardContent,
    CardActions,
    Box,
    Typography,
    Chip,
    Link,
    Button,
    IconButton,
    Tooltip,
    Divider,
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
  } from '@mui/material';
  import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
  import CloseIcon from '@mui/icons-material/Close';
  import type { Tender } from '../lib/api';

  export function formatDate(value: string | null): string {
    if (!value) return 'Not specified';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleDateString('en-AU', { year: 'numeric', month: 'short', day: 'numeric' });
  }

  export function formatMoney(tender: Tender): string {
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

  function StarIcon({ size = 20 }: { size?: number }) {
    return (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width={size}
        height={size}
        viewBox="0 0 256 256"
        fill="currentColor"
        aria-hidden="true"
        focusable="false"
      >
        <path d="M234.29,114.85l-45,38.83L203,211.75a16.4,16.4,0,0,1-24.5,17.82L128,198.49,77.47,229.57A16.4,16.4,0,0,1,53,211.75l13.76-58.07-45-38.83A16.46,16.46,0,0,1,31.08,86l59-4.76,22.76-55.08a16.36,16.36,0,0,1,30.27,0l22.75,55.08,59,4.76a16.46,16.46,0,0,1,9.37,28.86Z" />
      </svg>
    );
  }

  function isRecentlySeen(tender: Tender): boolean {
    if (!tender.first_seen_at) return false;
    const ageDays = (Date.now() - new Date(tender.first_seen_at).getTime()) / 86_400_000;
    return ageDays <= 7;
  }

  export function TenderCard({
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
      <Card sx={{ mb: 2, border: '1px solid', borderColor: 'divider', borderRadius: 2, boxShadow: '0 2px 4px rgba(0,0,0,0.04)' }}>
        <CardContent sx={{ pb: 1 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1.5 }}>
            <Typography variant="h6" component="div" sx={{ textAlign: 'left', fontWeight: 600, fontSize: '1.1rem' }}>
              {tender.title || 'Untitled Tender'}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexShrink: 0, ml: 1 }}>
              {isRecentlySeen(tender) && <Chip label="NEW" color="primary" size="small" />}
              <Tooltip title={isFavorite ? 'Remove from favourites' : 'Add to favourites'}>
                <IconButton
                  size="small"
                  aria-label={isFavorite ? 'Remove from favourites' : 'Add to favourites'}
                  aria-pressed={isFavorite}
                  onClick={() => onToggleFavorite(tender.tender_id)}
                  sx={{
                    width: 44,
                    height: 44,
                    color: isFavorite ? 'warning.main' : 'grey.400',
                    transition: 'color 150ms ease',
                    '&:hover': { color: isFavorite ? 'warning.dark' : 'warning.light', bgcolor: 'transparent' },
                  }}
                >
                  <StarIcon />
                </IconButton>
              </Tooltip>
            </Box>
          </Box>
  
          <Box
            sx={{
              p: 1.5,
              mb: 2,
              bgcolor: (theme) => (theme.palette.mode === 'dark' ? 'rgba(25, 118, 210, 0.16)' : '#f4f7fb'),
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
                color: 'text.primary',
                fontSize: '0.875rem',
                lineHeight: 1.4,
                display: '-webkit-box',
                WebkitLineClamp: 3,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {tender.description ? tender.description.slice(0, 180) + '…' : 'No AI summary generated for this tender yet.'}
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
          <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5, overflowWrap: 'anywhere' }}>
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
          <Button size="small" variant="text" onClick={() => onOpenDetails(tender)} sx={{ minHeight: 44, px: 2 }}>
            View More
          </Button>
        </CardActions>
      </Card>
    );
  }
  
  export function TenderDetailModal({
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
        slotProps={{ backdrop: { sx: { backgroundColor: 'rgba(0, 0, 0, 0.55)' } } }}
      >
        <DialogTitle sx={{ m: 0, p: 2.5, pr: 6, fontWeight: 600 }}>
          {tender.title || 'Tender Details'}
          <IconButton aria-label="close" onClick={onClose} sx={{ position: 'absolute', right: 12, top: 12, color: (theme) => theme.palette.grey[500] }}>
            <CloseIcon />
          </IconButton>
        </DialogTitle>
        <Divider />
        <DialogContent dividers sx={{ p: 3, textAlign: 'left' }}>
          <Box
            sx={{
              p: 2,
              mb: 3,
              bgcolor: (theme) => (theme.palette.mode === 'dark' ? 'rgba(25, 118, 210, 0.16)' : '#f4f7fb'),
              borderRadius: 1.5,
              borderLeft: '4px solid #1976d2',
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
              <AutoAwesomeIcon sx={{ fontSize: 18, color: '#1976d2' }} />
              <Typography variant="subtitle2" sx={{ fontWeight: 700, color: '#1976d2' }}>
                AI Summary & Insights
              </Typography>
            </Box>
            <Typography variant="body2" sx={{ color: 'text.primary', lineHeight: 1.6 }}>
              {tender.description ? tender.description.slice(0, 300) + '…' : 'Summary placeholder: Full key requirements, timeline, and scope summary will appear here once the AI enrichment pipeline is run.'}
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
          <Box sx={{ p: 2, bgcolor: 'background.default', border: '1px solid', borderColor: 'divider', borderRadius: 1, maxHeight: '300px', overflowY: 'auto', whiteSpace: 'pre-wrap', fontSize: '0.875rem', lineHeight: 1.6 }}>
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
