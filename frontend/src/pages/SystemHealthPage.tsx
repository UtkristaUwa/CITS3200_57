import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';

interface TableStateRowProps {
  colSpan: number;
  unavailableMessage: string;
  isLoading?: boolean;
  errorMessage?: string | null;
}

function TableStateRow({
  colSpan,
  unavailableMessage,
  isLoading = false,
  errorMessage = null,
}: TableStateRowProps) {
  return (
    <TableRow>
      <TableCell colSpan={colSpan} sx={{ py: 3 }}>
        {isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 1 }}>
            <CircularProgress size={20} />
            <Typography variant="body2" color="text.secondary">
              Loading...
            </Typography>
          </Box>
        ) : errorMessage ? (
          <Alert severity="error" sx={{ overflowWrap: 'anywhere' }}>{errorMessage}</Alert>
        ) : (
          <Alert severity="info" sx={{ overflowWrap: 'anywhere' }}>{unavailableMessage}</Alert>
        )}
      </TableCell>
    </TableRow>
  );
}

export default function SystemHealthPage() {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 1, overflowWrap: 'anywhere' }}>
        Scraper monitoring / system health
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Monitor configured website sources and scraper run outcomes when the required backend APIs become available.
      </Typography>

      <Paper component="section" sx={{ p: { xs: 2, sm: 3 }, minWidth: 0 }}>
        <Typography variant="h6" sx={{ fontWeight: 600, mb: 1 }}>
          Website sources / scraper status
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
          sx={{ maxWidth: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch', border: '1px solid', borderColor: 'divider', borderRadius: 1 }}
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
              <TableStateRow
                colSpan={5}
                unavailableMessage="Website source and scraper status APIs are not available yet."
              />
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </Box>
  );
}
