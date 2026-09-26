import { useCallback, useEffect, useState, type ReactNode } from 'react';
import axios from 'axios';
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
import {
  getModelConfig,
  updateModelConfig,
  type ModelConfigResponse,
  type ModelConfigUpdate,
} from '../lib/api';

interface ConfigurationSectionProps {
  title: string;
  description: string;
  children: ReactNode;
  statusLabel?: string;
  isLoading?: boolean;
  errorMessage?: string | null;
}

const reloadAlertSx = {
  mb: 2,
  flexWrap: { xs: 'wrap', sm: 'nowrap' },
  '& .MuiAlert-message': { minWidth: 0 },
  '& .MuiAlert-action': {
    flexBasis: { xs: '100%', sm: 'auto' },
    justifyContent: { xs: 'flex-end', sm: 'initial' },
    ml: { xs: 0, sm: 'auto' },
    pl: { xs: 0, sm: 2 },
    pt: { xs: 1, sm: 0.5 },
  },
  '& .MuiAlert-action .MuiButton-root': { whiteSpace: 'nowrap' },
};

function ConfigurationSection({
  title,
  description,
  children,
  statusLabel,
  isLoading = false,
  errorMessage = null,
}: ConfigurationSectionProps) {
  return (
    <Paper component="section" sx={{ p: { xs: 2, sm: 3 }, mb: 3, minWidth: 0, overflowWrap: 'anywhere' }}>
      <Box
        sx={{
          display: 'flex',
          flexDirection: { xs: 'column', sm: 'row' },
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: 1,
          mb: 1,
        }}
      >
        <Typography variant="h6" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        {statusLabel && <Chip label={statusLabel} size="small" color="warning" variant="outlined" sx={{ flexShrink: 0 }} />}
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
  const [triageModel, setTriageModel] = useState('');
  const [extractionModel, setExtractionModel] = useState('');
  const [generation, setGeneration] = useState<string | null>(null);
  const [originalValues, setOriginalValues] = useState<ModelConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [hasConflict, setHasConflict] = useState(false);

  const loadModelConfig = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setSaveError(null);
    setSaveSuccess(null);
    setHasConflict(false);

    try {
      const config = await getModelConfig();
      setTriageModel(config.triage_model);
      setExtractionModel(config.extraction_model);
      setGeneration(config.generation);
      setOriginalValues(config);
    } catch (error) {
      setTriageModel('');
      setExtractionModel('');
      setGeneration(null);
      setOriginalValues(null);

      if (axios.isAxiosError(error) && error.response?.status === 503) {
        setLoadError('Runtime model configuration is currently unavailable or not configured.');
      } else if (axios.isAxiosError(error) && error.response?.status === 401) {
        setLoadError('Your session has expired. Please sign in again.');
      } else if (axios.isAxiosError(error) && error.response?.status === 403) {
        setLoadError('You do not have permission to view model configuration.');
      } else {
        setLoadError('Unable to load the model configuration. Please try again.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadModelConfig();
  }, [loadModelConfig]);

  const trimmedTriageModel = triageModel.trim();
  const trimmedExtractionModel = extractionModel.trim();
  const triageChanged = originalValues !== null && trimmedTriageModel !== originalValues.triage_model;
  const extractionChanged =
    originalValues !== null && trimmedExtractionModel !== originalValues.extraction_model;
  const hasChanges = triageChanged || extractionChanged;
  const hasBlankModel = !trimmedTriageModel || !trimmedExtractionModel;
  const saveDisabled =
    loading || saving || !generation || !hasChanges || hasBlankModel || hasConflict;

  const handleSave = async () => {
    if (saveDisabled || !generation) {
      return;
    }

    const update: ModelConfigUpdate = { generation };
    if (triageChanged) {
      update.triage_model = trimmedTriageModel;
    }
    if (extractionChanged) {
      update.extraction_model = trimmedExtractionModel;
    }

    setSaving(true);
    setSaveError(null);
    setSaveSuccess(null);

    try {
      const config = await updateModelConfig(update);
      setTriageModel(config.triage_model);
      setExtractionModel(config.extraction_model);
      setGeneration(config.generation);
      setOriginalValues(config);
      setHasConflict(false);
      setSaveSuccess('Model configuration saved successfully.');
    } catch (error) {
      const status = axios.isAxiosError(error) ? error.response?.status : undefined;
      if (status === 409) {
        setHasConflict(true);
        setSaveError('The model configuration was changed by another update. Reload the latest values and try again.');
      } else if (status === 422) {
        setSaveError('One or more model identifiers are invalid. Check the values and try again.');
      } else if (status === 503) {
        setSaveError('Runtime model configuration is currently unavailable. Your changes have not been discarded.');
      } else if (status === 401) {
        setSaveError('Your session has expired. Please sign in again.');
      } else if (status === 403) {
        setSaveError('You do not have permission to update model configuration.');
      } else {
        setSaveError('Unable to save the model configuration. Please try again.');
      }
    } finally {
      setSaving(false);
    }
  };

  const handleModelChange = (setter: (value: string) => void, value: string) => {
    setter(value);
    setSaveSuccess(null);
    if (!hasConflict) {
      setSaveError(null);
    }
  };

  return (
    <Box sx={{ width: '100%', maxWidth: 1000, minWidth: 0 }}>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 1 }}>
        AI Configuration
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Review the AI settings used by the tender processing pipeline.
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
          <Button variant="contained" disabled sx={{ width: { xs: '100%', sm: 'auto' }, minHeight: { xs: 44, sm: 36 } }}>
            Save prompt
          </Button>
        </Box>
      </ConfigurationSection>

      <ConfigurationSection
        title="LLM Models"
        description="Model identifiers used by the tender triage, extraction and summarisation workflows."
      >
        {loading && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <CircularProgress size={20} />
            <Typography variant="body2" color="text.secondary">
              Loading model configuration...
            </Typography>
          </Box>
        )}
        {loadError && (
          <Alert
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={() => void loadModelConfig()} disabled={loading}>
                Reload
              </Button>
            }
            sx={reloadAlertSx}
          >
            {loadError}
          </Alert>
        )}
        {saveError && (
          <Alert
            severity={hasConflict ? 'warning' : 'error'}
            action={
              hasConflict ? (
                <Button color="inherit" size="small" onClick={() => void loadModelConfig()} disabled={loading}>
                  Reload
                </Button>
              ) : undefined
            }
            sx={reloadAlertSx}
          >
            {saveError}
          </Alert>
        )}
        {saveSuccess && (
          <Alert severity="success" sx={{ mb: 2 }}>
            {saveSuccess}
          </Alert>
        )}
        <TextField
          label="Document Triage Model"
          value={triageModel}
          onChange={(event) => handleModelChange(setTriageModel, event.target.value)}
          fullWidth
          disabled={loading || saving || !generation}
          error={!loading && !!generation && !trimmedTriageModel}
          helperText="Used to decide which tender documents should continue to AI processing."
          sx={{ mb: 2 }}
        />
        <TextField
          label="Extraction / Summarisation Model"
          value={extractionModel}
          onChange={(event) => handleModelChange(setExtractionModel, event.target.value)}
          fullWidth
          disabled={loading || saving || !generation}
          error={!loading && !!generation && !trimmedExtractionModel}
          helperText="Used for tender summarisation and structured field extraction."
        />
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
          <Button
            variant="contained"
            disabled={saveDisabled}
            onClick={() => void handleSave()}
            sx={{ width: { xs: '100%', sm: 'auto' }, minHeight: { xs: 44, sm: 36 } }}
          >
            {saving ? <CircularProgress size={20} color="inherit" /> : 'Save models'}
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
