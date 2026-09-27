import { useId } from 'react';
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
    Collapse,
    Divider,
  } from '@mui/material';
  import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
  import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
  import type { Tender } from '../lib/api';
  import { TenderDocuments } from './TenderDocuments';

  const BRAND_COLORS = {
    blue: '#2D3AF1',
    lilac: '#CF9EFF',
    orange: '#FF7C00',
  } as const;

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

  export function TenderCard({
    tender,
    isNew = false,
    isFavorite,
    onToggleFavorite,
    expanded,
    onToggleExpand,
  }: {
    tender: Tender;
    isNew?: boolean;
    isFavorite: boolean;
    onToggleFavorite: (tenderId: string) => void;
    expanded: boolean;
    onToggleExpand: (tenderId: string) => void;
  }) {
    const detailsId = useId();

    return (
      <Card
        sx={{
          mb: 2,
          minWidth: 0,
          border: '1px solid',
          borderColor: 'secondary.main',
          borderRadius: 2,
          boxShadow: (theme) => theme.palette.mode === 'light'
            ? '0 4px 14px rgba(36,45,50,0.08)'
            : 'none',
        }}
      >
        <CardContent sx={{ pb: 1 }}>
          <Box
            sx={{
              display: 'flex',
              flexDirection: { xs: 'column', sm: 'row' },
              justifyContent: 'space-between',
              alignItems: 'flex-start',
              gap: { xs: 1, sm: 0 },
              minWidth: 0,
              mb: 1.5,
            }}
          >
            <Typography
              variant="h6"
              component="div"
              sx={{ minWidth: 0, textAlign: 'left', fontWeight: 700, fontSize: '1.1rem', overflowWrap: 'anywhere' }}
            >
              {tender.title || 'Untitled tender'}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 1, flexShrink: 0, ml: { xs: 0, sm: 1 } }}>
              {isNew && <Chip label="New" color="primary" size="small" />}
              <Tooltip title={isFavorite ? 'Remove from favourites' : 'Add to favourites'}>
                <IconButton
                  size="small"
                  aria-label={isFavorite ? 'Remove from favourites' : 'Add to favourites'}
                  aria-pressed={isFavorite}
                  onClick={() => onToggleFavorite(tender.tender_id)}
                  sx={{
                    width: 44,
                    height: 44,
                    color: isFavorite ? BRAND_COLORS.orange : BRAND_COLORS.blue,
                    transition: 'color 150ms ease',
                    '&:hover': {
                      color: isFavorite ? BRAND_COLORS.orange : BRAND_COLORS.blue,
                      bgcolor: (theme) => theme.palette.mode === 'light' ? 'rgba(207,158,255,0.28)' : 'rgba(207,158,255,0.16)',
                    },
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
              bgcolor: (theme) => (theme.palette.mode === 'dark' ? 'rgba(207,158,255,0.12)' : 'rgba(207,158,255,0.24)'),
              borderRadius: 1.5,
              borderLeft: `4px solid ${BRAND_COLORS.blue}`,
              textAlign: 'left',
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
              <AutoAwesomeIcon sx={{ fontSize: 16, color: (theme) => theme.palette.mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.lilac }} />
              <Typography variant="caption" sx={{ fontWeight: 700, color: (theme) => theme.palette.mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.lilac }}>
                AI summary
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
                overflowWrap: 'anywhere',
              }}
            >
              {tender.summary_headline ?? (tender.description ? tender.description.slice(0, 180) + '…' : 'No AI summary generated for this tender yet.')}
            </Typography>
          </Box>

          <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5, overflowWrap: 'anywhere' }}>
            <strong>ATM ID:</strong> {tender.source_reference_id ?? 'Not specified'}
          </Typography>
          <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5, overflowWrap: 'anywhere' }}>
            <strong>Closing Date:</strong> {formatDate(tender.closing_date)}
          </Typography>
          <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5, overflowWrap: 'anywhere' }}>
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

        <Collapse in={expanded} timeout={300} id={detailsId}>
          <Divider sx={{ mx: 2 }} />
          <Box sx={{ px: { xs: 1.5, sm: 2 }, pt: 2, minWidth: 0, textAlign: 'left', overflowWrap: 'anywhere' }}>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1.5, mb: 2.5 }}>
              <Typography variant="body2"><strong>Monetary Value:</strong> {formatMoney(tender)}</Typography>
              <Typography variant="body2"><strong>Opening Date:</strong> {formatDate(tender.publish_date)}</Typography>
              <Typography variant="body2"><strong>Location:</strong> {tender.location ?? 'Not specified'}</Typography>
              <Typography variant="body2"><strong>Category:</strong> {tender.category ?? 'Not specified'}</Typography>
              <Typography variant="body2"><strong>Status:</strong> {tender.status ?? 'Active'}</Typography>
            </Box>

            <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>Full tender description</Typography>
            <Typography
              variant="body2"
              component="div"
              sx={{
                p: { xs: 1.5, sm: 2 },
                bgcolor: 'background.default',
                border: '1px solid',
                borderColor: 'divider',
                borderRadius: 1,
                maxHeight: { xs: 'none', sm: 300 },
                overflowY: { xs: 'visible', sm: 'auto' },
                whiteSpace: 'pre-wrap',
                overflowWrap: 'anywhere',
                lineHeight: 1.6,
                color: 'text.primary',
              }}
            >
              {tender.description ?? 'No description extracted for this tender.'}
            </Typography>
            <TenderDocuments documents={tender.documents} />
          </Box>
        </Collapse>

        <CardActions sx={{ justifyContent: 'center', pt: 1, pb: 1.5 }}>
          <Button
            size="small"
            variant="text"
            onClick={() => onToggleExpand(tender.tender_id)}
            sx={{ minHeight: 44, px: 2 }}
            aria-expanded={expanded}
            aria-controls={detailsId}
            endIcon={
              <ExpandMoreIcon
                sx={{ transition: 'transform 300ms ease', transform: expanded ? 'rotate(180deg)' : 'none' }}
              />
            }
          >
            {expanded ? 'View Less' : 'View More'}
          </Button>
        </CardActions>
      </Card>
    );
  }
