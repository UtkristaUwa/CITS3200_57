import type { ReactNode } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  TextField,
  Typography,
} from '@mui/material';

interface ConfigurationSectionProps {
  title: string;
  description: string;
  children: ReactNode;
  statusLabel?: string;
  isLoading?: boolean;
  errorMessage?: string | null;
}

function ConfigurationSection({
  title,
  description,
  children,
  statusLabel,
  isLoading = false,
  errorMessage = null,
}: ConfigurationSectionProps) {
  return (
    <Paper component="section" sx={{ p: 3, mb: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 2, mb: 1 }}>
        <Typography variant="h6" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        {statusLabel && <Chip label={statusLabel} size="small" color="warning" variant="outlined" />}
      </Box>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {description}
      </Typography>

      {isLoading && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <CircularProgress size={20} />
          <Typography variant="body2" color="text.secondary">
            Loading configuration...
          </Typography>
        </Box>
      )}

      {errorMessage && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {errorMessage}
        </Alert>
      )}

      {children}
    </Paper>
  );
}

export default function ConfigPage() {
  return (
    <Box sx={{ maxWidth: 1000 }}>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 1 }}>
        AI Configuration
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Review the AI settings used by the tender processing pipeline. Editing will be enabled when
        the backend configuration API is available.
      </Typography>

      <ConfigurationSection
        title="Tender Extraction Prompt"
        description="System instructions used to extract structured database fields from scraped tender content."
      >
        <Alert severity="info" sx={{ mb: 2 }}>
          Backend configuration API not available. The current prompt cannot be loaded or updated here yet, so
          this preview is intentionally empty.
        </Alert>
        <TextField
          label="Extraction prompt preview"
          placeholder="The current extraction prompt will appear here when the backend API is available."
          multiline
          minRows={8}
          fullWidth
          slotProps={{ input: { readOnly: true } }}
          helperText="Display only. This page does not read or modify tender_processor.cfg directly."
        />
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
          <Button variant="contained" disabled>
            Save prompt
          </Button>
        </Box>
      </ConfigurationSection>

      <ConfigurationSection
        title="LLM Model"
        description="Model identifier used by the tender extraction workflow."
      >
        <Alert severity="info" sx={{ mb: 2 }}>
          Backend configuration API not available. This is the current known configuration and cannot be updated
          here yet.
        </Alert>
        <TextField
          label="Current configured model (display only)"
          value="gemini-2.5-flash"
          fullWidth
          slotProps={{ input: { readOnly: true } }}
          helperText="Available models may vary by deployment region."
        />
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
          <Button variant="contained" disabled>
            Save model
          </Button>
        </Box>
      </ConfigurationSection>

      <ConfigurationSection
        title="Tender Relevance Prompt"
        description="Instructions for deciding whether a tender is relevant to the organisation."
        statusLabel="Not available yet"
      >
        <Alert severity="warning" sx={{ mb: 2 }}>
          Pending relevance backend implementation. Document triage is a separate process and is not used here.
        </Alert>
        <TextField
          label="Relevance prompt"
          placeholder="A relevance prompt can be configured after the relevance backend is implemented."
          multiline
          minRows={6}
          fullWidth
          disabled
        />
      </ConfigurationSection>
    </Box>
  );
}
