import React, { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Link,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography
} from '@mui/material';

interface ScraperHealthRecord {
  website: string;
  url: string;
  last_run: string;
  status: string;
  status_color: 'success' | 'error' | 'warning' | 'default';
  message: string;
}

const STATUS_JSON_URL =
  'https://storage.googleapis.com/tenderai-dev-documents/scraper_health.json';

export default function SystemHealthPage() {
  const [statuses, setStatuses] = useState<ScraperHealthRecord[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHealthStatus = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${STATUS_JSON_URL}?t=${Date.now()}`);
      if (!res.ok) {
        throw new Error(
          res.status === 404
            ? 'No scraper health data found yet. The pipeline has not run its initial execution.'
            : `Failed to load status (${res.status} ${res.statusText})`
        );
      }
      const data: ScraperHealthRecord[] = await res.json();
      setStatuses(data);
    } catch (err: unknown) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Failed to fetch scraper status.');
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealthStatus();
  }, []);

  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 1, overflowWrap: 'anywhere' }}>
        Scraper Monitoring / System Health
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Monitor configured website sources and scraper run outcomes.
      </Typography>

      <Paper component="section" sx={{ p: { xs: 2, sm: 3 }, minWidth: 0 }}>
        <Typography variant="h6" sx={{ fontWeight: 600, mb: 1 }}>
          Website Sources / Scraper Status
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Latest scraper run status and error output for each configured website source.
        </Typography>

        <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 1, mb: 2 }}>
          <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
            Status legend:
          </Typography>
          <Chip label="Success" color="success" size="small" variant="outlined" />
          <Chip label="Error" color="error" size="small" variant="outlined" />
          <Chip label="Failed to download" color="warning" size="small" variant="outlined" />
          <Chip label="Unknown" size="small" variant="outlined" />
        </Box>

        <TableContainer
          tabIndex={0}
          sx={{
            maxWidth: '100%',
            overflowX: 'auto',
            WebkitOverflowScrolling: 'touch',
            border: '1px solid',
            borderColor: 'divider',
            borderRadius: 1
          }}
        >
          <Table size="small" aria-label="Website sources and scraper run status" sx={{ minWidth: 760 }}>
            <TableHead>
              <TableRow>
                <TableCell>Website</TableCell>
                <TableCell>URL</TableCell>
                <TableCell>Last Run</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Message</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {loading && (
                <TableRow>
                  <TableCell colSpan={5} align="center" sx={{ py: 3 }}>
                    <CircularProgress size={24} />
                    <Typography variant="body2" sx={{ mt: 1 }} color="text.secondary">
                      Loading scraper health data...
                    </Typography>
                  </TableCell>
                </TableRow>
              )}

              {!loading && error && (
                <TableRow>
                  <TableCell colSpan={5} sx={{ p: 2 }}>
                    <Alert severity="warning">{error}</Alert>
                  </TableCell>
                </TableRow>
              )}

              {!loading && !error && statuses.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} align="center" sx={{ py: 3 }}>
                    <Typography variant="body2" color="text.secondary">
                      No scraper logs recorded.
                    </Typography>
                  </TableCell>
                </TableRow>
              )}

              {!loading &&
                !error &&
                statuses.map((row) => (
                  <TableRow key={row.website} hover>
                    <TableCell sx={{ fontWeight: 600 }}>{row.website}</TableCell>
                    <TableCell>
                      {row.url && row.url !== 'N/A' ? (
                        <Link
                          href={row.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          underline="hover"
                        >
                          {row.url}
                        </Link>
                      ) : (
                        'N/A'
                      )}
                    </TableCell>
                    <TableCell>
                      {row.last_run
                        ? new Date(row.last_run).toLocaleString('en-AU', {
                            dateStyle: 'medium',
                            timeStyle: 'short',
                          })
                        : 'N/A'}
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={row.status}
                        color={row.status_color || 'default'}
                        size="small"
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell sx={{ color: 'text.secondary', maxWidth: 300 }}>
                      {row.message}
                    </TableCell>
                  </TableRow>
                ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </Box>
  );
}