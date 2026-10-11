import { useCallback, useEffect, useState, type ReactNode } from 'react';
import axios from 'axios';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Paper,
  TextField,
  Typography,
} from '@mui/material';
import {
  getPromptsConfig,
  getRelevanceConfig,
  getModelConfig,
  startReprocessRun,
  updatePromptsConfig,
  updateRelevanceConfig,
  updateModelConfig,
  type ModelConfigResponse,
  type ModelConfigUpdate,
  type PromptsConfigResponse,
  type PromptsConfigUpdate,
  type RelevanceConfigResponse,
  type RelevanceConfigUpdate,
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

type RelevanceEditableConfig = Omit<RelevanceConfigResponse, 'generation'>;
type RelevanceFormValues = Record<keyof RelevanceEditableConfig, string>;
type RelevanceFormErrors = Partial<Record<keyof RelevanceFormValues, string>>;

const EMPTY_RELEVANCE_FORM: RelevanceFormValues = {
  classification_thoughts: '',
  focus_areas: '',
  work_types: '',
  out_of_scope: '',
  focus_area_weight: '',
  work_type_weight: '',
  recency_weight: '',
  recency_horizon_days: '',
  out_of_scope_fit_cap: '',
  max_focus_areas: '',
  max_work_types: '',
  relevance_model: '',
  relevance_temperature: '',
};

const RELEVANCE_FIELDS = Object.keys(EMPTY_RELEVANCE_FORM) as (keyof RelevanceEditableConfig)[];
const RELEVANCE_PROMPT_FIELDS = [
  'classification_thoughts',
  'focus_areas',
  'work_types',
  'out_of_scope',
] as const;

function toRelevanceForm(config: RelevanceConfigResponse): RelevanceFormValues {
  return {
    classification_thoughts: config.classification_thoughts,
    focus_areas: config.focus_areas,
    work_types: config.work_types,
    out_of_scope: config.out_of_scope,
    focus_area_weight: String(config.focus_area_weight),
    work_type_weight: String(config.work_type_weight),
    recency_weight: String(config.recency_weight),
    recency_horizon_days: String(config.recency_horizon_days),
    out_of_scope_fit_cap: String(config.out_of_scope_fit_cap),
    max_focus_areas: String(config.max_focus_areas),
    max_work_types: String(config.max_work_types),
    relevance_model: config.relevance_model,
    relevance_temperature: String(config.relevance_temperature),
  };
}

// The three prompt panes all edit one GCS object, which carries a single
// generation token. They therefore share one load and one generation: saving
// any pane hands the new generation to all of them, so the others don't go
// stale and 409 on their next save.
type PromptField = 'field_extraction' | 'summary' | 'doc_triage' | 'triage_char_limit';
type PromptsFormValues = Record<PromptField, string>;
type PromptsFormErrors = Partial<Record<PromptField, string>>;
type PromptPane = 'extraction' | 'summary' | 'triage';
type PaneMessages = Partial<Record<PromptPane, string>>;

const EMPTY_PROMPTS_FORM: PromptsFormValues = {
  field_extraction: '',
  summary: '',
  doc_triage: '',
  triage_char_limit: '',
};

const PANE_FIELDS: Record<PromptPane, PromptField[]> = {
  extraction: ['field_extraction'],
  summary: ['summary'],
  triage: ['doc_triage', 'triage_char_limit'],
};

const PANE_LABELS: Record<PromptPane, string> = {
  extraction: 'Extraction prompt',
  summary: 'Summary prompt',
  triage: 'Triage configuration',
};

function toPromptsForm(config: PromptsConfigResponse): PromptsFormValues {
  return {
    field_extraction: config.field_extraction,
    summary: config.summary,
    doc_triage: config.doc_triage,
    triage_char_limit: String(config.triage_char_limit),
  };
}

function validatePromptsForm(values: PromptsFormValues): PromptsFormErrors {
  const errors: PromptsFormErrors = {};

  for (const field of ['field_extraction', 'summary', 'doc_triage'] as const) {
    if (!values[field].trim()) {
      errors[field] = 'This field is required.';
    } else if (values[field].split(/\r?\n/).some((line) => /^[#;]/.test(line.trimStart()))) {
      // The config parser treats such a line as a comment and would drop it.
      errors[field] = 'Lines cannot begin with # or ;.';
    }
  }

  const charLimit = Number(values.triage_char_limit);
  if (!values.triage_char_limit.trim() || !Number.isInteger(charLimit) || charLimit <= 0) {
    errors.triage_char_limit = 'Enter a whole number greater than zero.';
  }

  return errors;
}

function validateRelevanceForm(values: RelevanceFormValues): RelevanceFormErrors {
  const errors: RelevanceFormErrors = {};

  for (const field of RELEVANCE_PROMPT_FIELDS) {
    if (!values[field].trim()) {
      errors[field] = 'This field is required.';
    } else if (values[field].split(/\r?\n/).some((line) => /^[#;]/.test(line.trimStart()))) {
      errors[field] = 'Lines cannot begin with # or ;.';
    }
  }
  if (!values.relevance_model.trim()) {
    errors.relevance_model = 'This field is required.';
  }

  const numberValue = (field: keyof RelevanceFormValues): number => Number(values[field]);
  const requireFinite = (field: keyof RelevanceFormValues): number | null => {
    if (!values[field].trim() || !Number.isFinite(numberValue(field))) {
      errors[field] = 'Enter a valid number.';
      return null;
    }
    return numberValue(field);
  };

  const focusWeight = requireFinite('focus_area_weight');
  const workWeight = requireFinite('work_type_weight');
  if (focusWeight !== null && (focusWeight < 0 || focusWeight > 1)) {
    errors.focus_area_weight = 'Enter a value from 0 to 1.';
  }
  if (workWeight !== null && (workWeight < 0 || workWeight > 1)) {
    errors.work_type_weight = 'Enter a value from 0 to 1.';
  }
  if (
    focusWeight !== null
    && workWeight !== null
    && !errors.focus_area_weight
    && !errors.work_type_weight
    && Math.abs(focusWeight + workWeight - 1) > 1e-9
  ) {
    errors.focus_area_weight = 'The two weights must add up to 1.';
    errors.work_type_weight = 'The two weights must add up to 1.';
  }

  const recencyWeight = requireFinite('recency_weight');
  if (recencyWeight !== null && recencyWeight < 0) {
    errors.recency_weight = 'Enter a value of 0 or greater.';
  }
  const temperature = requireFinite('relevance_temperature');
  if (temperature !== null && temperature < 0) {
    errors.relevance_temperature = 'Enter a value of 0 or greater.';
  }

  const positiveIntegerFields = [
    'recency_horizon_days',
    'max_focus_areas',
    'max_work_types',
  ] as const;
  for (const field of positiveIntegerFields) {
    const value = requireFinite(field);
    if (value !== null && (!Number.isInteger(value) || value <= 0)) {
      errors[field] = 'Enter a whole number greater than 0.';
    }
  }

  const fitCap = requireFinite('out_of_scope_fit_cap');
  if (fitCap !== null && (!Number.isInteger(fitCap) || fitCap < 0 || fitCap > 100)) {
    errors.out_of_scope_fit_cap = 'Enter a whole number from 0 to 100.';
  }

  return errors;
}

function normaliseRelevanceForm(values: RelevanceFormValues): RelevanceEditableConfig {
  return {
    classification_thoughts: values.classification_thoughts.trim(),
    focus_areas: values.focus_areas.trim(),
    work_types: values.work_types.trim(),
    out_of_scope: values.out_of_scope.trim(),
    focus_area_weight: Number(values.focus_area_weight),
    work_type_weight: Number(values.work_type_weight),
    recency_weight: Number(values.recency_weight),
    recency_horizon_days: Number(values.recency_horizon_days),
    out_of_scope_fit_cap: Number(values.out_of_scope_fit_cap),
    max_focus_areas: Number(values.max_focus_areas),
    max_work_types: Number(values.max_work_types),
    relevance_model: values.relevance_model.trim(),
    relevance_temperature: Number(values.relevance_temperature),
  };
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
    <Paper
      component="section"
      sx={{
        p: { xs: 2, sm: 3 },
        mb: 3,
        minWidth: 0,
        overflowWrap: 'anywhere',
        bgcolor: 'background.paper',
        border: '1px solid',
        borderColor: 'secondary.main',
        borderLeft: '4px solid',
        borderLeftColor: 'primary.main',
        boxShadow: (theme) => theme.palette.mode === 'light'
          ? '0 4px 14px rgba(36,45,50,0.06)'
          : 'none',
      }}
    >
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
        <Typography
          variant="h6"
          sx={{
            fontWeight: 700,
            color: (theme) => theme.palette.mode === 'light' ? 'primary.main' : 'secondary.main',
          }}
        >
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
  // Every section on this page edits ONE object in GCS, which carries one
  // generation. Tracking a generation per section meant saving any section
  // left the others holding a stale one, so their next save 409'd until the
  // page was reloaded. Each load and each successful save refreshes this.
  const [configGeneration, setConfigGeneration] = useState<string | null>(null);
  const [originalValues, setOriginalValues] = useState<ModelConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [hasConflict, setHasConflict] = useState(false);
  const [relevanceForm, setRelevanceForm] = useState<RelevanceFormValues>(EMPTY_RELEVANCE_FORM);
  const [originalRelevance, setOriginalRelevance] = useState<RelevanceConfigResponse | null>(null);
  const [relevanceLoading, setRelevanceLoading] = useState(true);
  const [relevanceSaving, setRelevanceSaving] = useState(false);
  const [relevanceLoadError, setRelevanceLoadError] = useState<string | null>(null);
  const [relevanceSaveError, setRelevanceSaveError] = useState<string | null>(null);
  const [relevanceSaveSuccess, setRelevanceSaveSuccess] = useState<string | null>(null);
  const [relevanceHasConflict, setRelevanceHasConflict] = useState(false);
  const [promptsForm, setPromptsForm] = useState<PromptsFormValues>(EMPTY_PROMPTS_FORM);
  const [originalPrompts, setOriginalPrompts] = useState<PromptsConfigResponse | null>(null);
  const [promptsLoading, setPromptsLoading] = useState(true);
  const [promptsLoadError, setPromptsLoadError] = useState<string | null>(null);
  const [savingPane, setSavingPane] = useState<PromptPane | null>(null);
  const [paneError, setPaneError] = useState<PaneMessages>({});
  const [paneSuccess, setPaneSuccess] = useState<PaneMessages>({});
  const [conflictPane, setConflictPane] = useState<PromptPane | null>(null);
  const [reprocessConfirmOpen, setReprocessConfirmOpen] = useState(false);
  const [reprocessStarting, setReprocessStarting] = useState(false);
  const [reprocessError, setReprocessError] = useState<string | null>(null);
  const [reprocessStarted, setReprocessStarted] = useState(false);

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
      setConfigGeneration(config.generation);
      setOriginalValues(config);
    } catch (error) {
      setTriageModel('');
      setExtractionModel('');
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

  const loadPromptsConfig = useCallback(async () => {
    setPromptsLoading(true);
    setPromptsLoadError(null);
    setPaneError({});
    setPaneSuccess({});
    setConflictPane(null);

    try {
      const config = await getPromptsConfig();
      setPromptsForm(toPromptsForm(config));
      setConfigGeneration(config.generation);
      setOriginalPrompts(config);
    } catch (error) {
      setPromptsForm(EMPTY_PROMPTS_FORM);
      setOriginalPrompts(null);

      if (axios.isAxiosError(error) && error.response?.status === 503) {
        setPromptsLoadError('Runtime prompt configuration is currently unavailable or not configured.');
      } else if (axios.isAxiosError(error) && error.response?.status === 401) {
        setPromptsLoadError('Your session has expired. Please sign in again.');
      } else if (axios.isAxiosError(error) && error.response?.status === 403) {
        setPromptsLoadError('You do not have permission to view prompt configuration.');
      } else {
        setPromptsLoadError('Unable to load the prompt configuration. Please try again.');
      }
    } finally {
      setPromptsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPromptsConfig();
  }, [loadPromptsConfig]);

  const loadRelevanceConfig = useCallback(async () => {
    setRelevanceLoading(true);
    setRelevanceLoadError(null);
    setRelevanceSaveError(null);
    setRelevanceSaveSuccess(null);
    setRelevanceHasConflict(false);

    try {
      const config = await getRelevanceConfig();
      setRelevanceForm(toRelevanceForm(config));
      setConfigGeneration(config.generation);
      setOriginalRelevance(config);
    } catch (error) {
      setRelevanceForm(EMPTY_RELEVANCE_FORM);
      setOriginalRelevance(null);

      if (axios.isAxiosError(error) && error.response?.status === 503) {
        setRelevanceLoadError('Runtime relevance configuration is currently unavailable or not configured.');
      } else if (axios.isAxiosError(error) && error.response?.status === 401) {
        setRelevanceLoadError('Your session has expired. Please sign in again.');
      } else if (axios.isAxiosError(error) && error.response?.status === 403) {
        setRelevanceLoadError('You do not have permission to view relevance configuration.');
      } else {
        setRelevanceLoadError('Unable to load the relevance configuration. Please try again.');
      }
    } finally {
      setRelevanceLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRelevanceConfig();
  }, [loadRelevanceConfig]);

  const trimmedTriageModel = triageModel.trim();
  const trimmedExtractionModel = extractionModel.trim();
  const triageChanged = originalValues !== null && trimmedTriageModel !== originalValues.triage_model;
  const extractionChanged =
    originalValues !== null && trimmedExtractionModel !== originalValues.extraction_model;
  const hasChanges = triageChanged || extractionChanged;
  const hasBlankModel = !trimmedTriageModel || !trimmedExtractionModel;
  const saveDisabled =
    loading || saving || !configGeneration || !hasChanges || hasBlankModel || hasConflict;

  const handleSave = async () => {
    if (saveDisabled || !configGeneration) {
      return;
    }

    const update: ModelConfigUpdate = { generation: configGeneration };
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
      setConfigGeneration(config.generation);
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

  const promptFieldErrors = validatePromptsForm(promptsForm);
  const paneHasError = (pane: PromptPane) =>
    PANE_FIELDS[pane].some((field) => promptFieldErrors[field] !== undefined);
  const paneHasChanges = (pane: PromptPane) =>
    originalPrompts !== null &&
    PANE_FIELDS[pane].some(
      (field) => promptsForm[field].trim() !== String(originalPrompts[field]),
    );
  const paneSaveDisabled = (pane: PromptPane) =>
    promptsLoading ||
    savingPane !== null ||
    !configGeneration ||
    !paneHasChanges(pane) ||
    paneHasError(pane) ||
    conflictPane !== null;

  const handlePromptChange = (field: PromptField, value: string) => {
    setPromptsForm((current) => ({ ...current, [field]: value }));
    setPaneSuccess({});
    if (conflictPane === null) {
      setPaneError({});
    }
  };

  const handlePaneSave = async (pane: PromptPane) => {
    if (paneSaveDisabled(pane) || !configGeneration) {
      return;
    }

    const update: PromptsConfigUpdate = { generation: configGeneration };
    for (const field of PANE_FIELDS[pane]) {
      if (field === 'triage_char_limit') {
        update.triage_char_limit = Number(promptsForm.triage_char_limit.trim());
      } else {
        update[field] = promptsForm[field].trim();
      }
    }

    setSavingPane(pane);
    setPaneError({});
    setPaneSuccess({});

    try {
      const config = await updatePromptsConfig(update);
      // Only the saved pane snaps to the server's value. Rewriting the whole
      // form here would discard whatever the user had typed into the other
      // panes but not yet saved.
      const saved = toPromptsForm(config);
      setPromptsForm((current) => {
        const next = { ...current };
        for (const field of PANE_FIELDS[pane]) {
          next[field] = saved[field];
        }
        return next;
      });
      setConfigGeneration(config.generation);
      setOriginalPrompts(config);
      setConflictPane(null);
      setPaneSuccess({ [pane]: `${PANE_LABELS[pane]} saved successfully.` });
    } catch (error) {
      const status = axios.isAxiosError(error) ? error.response?.status : undefined;
      let message: string;
      if (status === 409) {
        setConflictPane(pane);
        message = 'The configuration was changed by another update. Reload the latest values and try again.';
      } else if (status === 422) {
        message = 'That value is invalid. Prompts cannot be empty or contain lines starting with # or ;.';
      } else if (status === 503) {
        message = 'Runtime configuration is currently unavailable. Your changes have not been discarded.';
      } else if (status === 401) {
        message = 'Your session has expired. Please sign in again.';
      } else if (status === 403) {
        message = 'You do not have permission to update prompt configuration.';
      } else {
        message = `Unable to save the ${PANE_LABELS[pane].toLowerCase()}. Please try again.`;
      }
      setPaneError({ [pane]: message });
    } finally {
      setSavingPane(null);
    }
  };

  const handleReprocessConfirm = async () => {
    setReprocessStarting(true);
    setReprocessError(null);
    setReprocessStarted(false);

    try {
      await startReprocessRun();
      setReprocessStarted(true);
      setReprocessConfirmOpen(false);
    } catch (error) {
      const status = axios.isAxiosError(error) ? error.response?.status : undefined;
      if (status === 409) {
        setReprocessError('A pipeline run is already in progress. Wait for it to finish, then try again.');
      } else if (status === 503) {
        setReprocessError('The pipeline job could not be started. Check that the job exists and this service can run it.');
      } else if (status === 401) {
        setReprocessError('Your session has expired. Please sign in again.');
      } else if (status === 403) {
        setReprocessError('You do not have permission to start a reprocess run.');
      } else {
        setReprocessError('Unable to start the reprocess run. Please try again.');
      }
    } finally {
      setReprocessStarting(false);
    }
  };

  const renderPaneAlerts = (pane: PromptPane) => (
    <>
      {promptsLoading && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <CircularProgress size={20} />
          <Typography variant="body2" color="text.secondary">
            Loading configuration...
          </Typography>
        </Box>
      )}
      {promptsLoadError && (
        <Alert
          severity="error"
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => void loadPromptsConfig()}
              disabled={promptsLoading}
            >
              Reload
            </Button>
          }
          sx={reloadAlertSx}
        >
          {promptsLoadError}
        </Alert>
      )}
      {paneError[pane] && (
        <Alert
          severity={conflictPane === pane ? 'warning' : 'error'}
          action={
            conflictPane === pane ? (
              <Button
                color="inherit"
                size="small"
                onClick={() => void loadPromptsConfig()}
                disabled={promptsLoading}
              >
                Reload
              </Button>
            ) : undefined
          }
          sx={reloadAlertSx}
        >
          {paneError[pane]}
        </Alert>
      )}
      {paneSuccess[pane] && (
        <Alert severity="success" sx={{ mb: 2 }}>
          {paneSuccess[pane]}
        </Alert>
      )}
    </>
  );

  const renderPaneSave = (pane: PromptPane) => (
    <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
      <Button
        variant="contained"
        onClick={() => void handlePaneSave(pane)}
        disabled={paneSaveDisabled(pane)}
        sx={{ width: { xs: '100%', sm: 'auto' }, minHeight: { xs: 44, sm: 36 } }}
      >
        {savingPane === pane ? 'Saving...' : 'Save prompt'}
      </Button>
    </Box>
  );

  const promptFieldDisabled = promptsLoading || savingPane !== null || !configGeneration;

  const handleModelChange = (setter: (value: string) => void, value: string) => {
    setter(value);
    setSaveSuccess(null);
    if (!hasConflict) {
      setSaveError(null);
    }
  };

  const relevanceErrors = validateRelevanceForm(relevanceForm);
  const displayedRelevanceErrors: RelevanceFormErrors =
    !relevanceLoading && configGeneration ? relevanceErrors : {};
  const normalisedRelevance = normaliseRelevanceForm(relevanceForm);
  const relevanceHasChanges = originalRelevance !== null && RELEVANCE_FIELDS.some(
    (field) => normalisedRelevance[field] !== originalRelevance[field],
  );
  const relevanceSaveDisabled =
    relevanceLoading
    || relevanceSaving
    || !configGeneration
    || !relevanceHasChanges
    || Object.keys(relevanceErrors).length > 0
    || relevanceHasConflict;

  const handleRelevanceChange = (field: keyof RelevanceFormValues, value: string) => {
    setRelevanceForm((current) => ({ ...current, [field]: value }));
    setRelevanceSaveSuccess(null);
    if (!relevanceHasConflict) {
      setRelevanceSaveError(null);
    }
  };

  const handleRelevanceSave = async () => {
    if (relevanceSaveDisabled || !configGeneration || !originalRelevance) {
      return;
    }

    const update: RelevanceConfigUpdate = { generation: configGeneration };
    if (normalisedRelevance.classification_thoughts !== originalRelevance.classification_thoughts) {
      update.classification_thoughts = normalisedRelevance.classification_thoughts;
    }
    if (normalisedRelevance.focus_areas !== originalRelevance.focus_areas) {
      update.focus_areas = normalisedRelevance.focus_areas;
    }
    if (normalisedRelevance.work_types !== originalRelevance.work_types) {
      update.work_types = normalisedRelevance.work_types;
    }
    if (normalisedRelevance.out_of_scope !== originalRelevance.out_of_scope) {
      update.out_of_scope = normalisedRelevance.out_of_scope;
    }
    if (normalisedRelevance.focus_area_weight !== originalRelevance.focus_area_weight) {
      update.focus_area_weight = normalisedRelevance.focus_area_weight;
    }
    if (normalisedRelevance.work_type_weight !== originalRelevance.work_type_weight) {
      update.work_type_weight = normalisedRelevance.work_type_weight;
    }
    if (normalisedRelevance.recency_weight !== originalRelevance.recency_weight) {
      update.recency_weight = normalisedRelevance.recency_weight;
    }
    if (normalisedRelevance.recency_horizon_days !== originalRelevance.recency_horizon_days) {
      update.recency_horizon_days = normalisedRelevance.recency_horizon_days;
    }
    if (normalisedRelevance.out_of_scope_fit_cap !== originalRelevance.out_of_scope_fit_cap) {
      update.out_of_scope_fit_cap = normalisedRelevance.out_of_scope_fit_cap;
    }
    if (normalisedRelevance.max_focus_areas !== originalRelevance.max_focus_areas) {
      update.max_focus_areas = normalisedRelevance.max_focus_areas;
    }
    if (normalisedRelevance.max_work_types !== originalRelevance.max_work_types) {
      update.max_work_types = normalisedRelevance.max_work_types;
    }
    if (normalisedRelevance.relevance_model !== originalRelevance.relevance_model) {
      update.relevance_model = normalisedRelevance.relevance_model;
    }
    if (normalisedRelevance.relevance_temperature !== originalRelevance.relevance_temperature) {
      update.relevance_temperature = normalisedRelevance.relevance_temperature;
    }

    setRelevanceSaving(true);
    setRelevanceSaveError(null);
    setRelevanceSaveSuccess(null);

    try {
      const config = await updateRelevanceConfig(update);
      setRelevanceForm(toRelevanceForm(config));
      setConfigGeneration(config.generation);
      setOriginalRelevance(config);
      setRelevanceHasConflict(false);
      setRelevanceSaveSuccess('Relevance configuration saved successfully.');
    } catch (error) {
      const status = axios.isAxiosError(error) ? error.response?.status : undefined;
      if (status === 409) {
        setRelevanceHasConflict(true);
        setRelevanceSaveError(
          'The relevance configuration was changed by another update. Reload the latest values and try again.',
        );
      } else if (status === 422) {
        setRelevanceSaveError('Check the values and try again.');
      } else if (status === 503) {
        setRelevanceSaveError(
          'Runtime relevance configuration is currently unavailable. Your changes have not been discarded.',
        );
      } else if (status === 401) {
        setRelevanceSaveError('Your session has expired. Please sign in again.');
      } else if (status === 403) {
        setRelevanceSaveError('You do not have permission to update relevance configuration.');
      } else {
        setRelevanceSaveError('Unable to save the relevance configuration. Please try again.');
      }
    } finally {
      setRelevanceSaving(false);
    }
  };

  return (
    <Box sx={{ width: '100%', maxWidth: 1000, minWidth: 0 }}>
      <Typography
        variant="h5"
        sx={{
          fontWeight: 700,
          mb: 1,
          color: (theme) => theme.palette.mode === 'light' ? 'primary.main' : 'secondary.main',
        }}
      >
        AI configuration
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Review the AI settings used by the tender processing pipeline.
      </Typography>

      <ConfigurationSection
        title="Tender extraction prompt"
        description="System instructions used to extract structured database fields from scraped tender content."
      >
        {renderPaneAlerts('extraction')}
        <TextField
          label="Extraction prompt"
          value={promptsForm.field_extraction}
          onChange={(event) => handlePromptChange('field_extraction', event.target.value)}
          multiline
          minRows={8}
          fullWidth
          disabled={promptFieldDisabled}
          error={promptFieldErrors.field_extraction !== undefined}
          helperText={
            promptFieldErrors.field_extraction
            ?? 'Saved to [system_prompts] field_extraction. Applies to the next pipeline run.'
          }
        />
        {renderPaneSave('extraction')}
      </ConfigurationSection>

      <ConfigurationSection
        title="Tender summary prompt"
        description="System instructions used to generate each tender's headline and description."
      >
        {renderPaneAlerts('summary')}
        <TextField
          label="Summary prompt"
          value={promptsForm.summary}
          onChange={(event) => handlePromptChange('summary', event.target.value)}
          multiline
          minRows={8}
          fullWidth
          disabled={promptFieldDisabled}
          error={promptFieldErrors.summary !== undefined}
          helperText={
            promptFieldErrors.summary
            ?? 'Saved to [system_prompts] summary. Applies to the next pipeline run.'
          }
        />
        {renderPaneSave('summary')}
      </ConfigurationSection>

      <ConfigurationSection
        title="Document triage"
        description="Decides which attachments are read at all. Documents dropped here are never seen by the extraction or summary steps."
      >
        {renderPaneAlerts('triage')}
        <TextField
          label="Triage prompt"
          value={promptsForm.doc_triage}
          onChange={(event) => handlePromptChange('doc_triage', event.target.value)}
          multiline
          minRows={8}
          fullWidth
          disabled={promptFieldDisabled}
          error={promptFieldErrors.doc_triage !== undefined}
          helperText={
            promptFieldErrors.doc_triage
            ?? 'Saved to [system_prompts] doc_triage. Applies to the next pipeline run.'
          }
        />
        <TextField
          label="Triage character limit"
          value={promptsForm.triage_char_limit}
          onChange={(event) => handlePromptChange('triage_char_limit', event.target.value)}
          type="number"
          fullWidth
          sx={{ mt: 2 }}
          disabled={promptFieldDisabled}
          error={promptFieldErrors.triage_char_limit !== undefined}
          helperText={
            promptFieldErrors.triage_char_limit
            ?? 'How many characters of each document the triage step reads before deciding. Saved to [models] triage_char_limit.'
          }
        />
        {renderPaneSave('triage')}
      </ConfigurationSection>

      <ConfigurationSection
        title="LLM models"
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
          disabled={loading || saving || !configGeneration}
          error={!loading && !!configGeneration && !trimmedTriageModel}
          helperText="Used to decide which tender documents should continue to AI processing."
          sx={{ mb: 2 }}
        />
        <TextField
          label="Extraction / Summarisation Model"
          value={extractionModel}
          onChange={(event) => handleModelChange(setExtractionModel, event.target.value)}
          fullWidth
          disabled={loading || saving || !configGeneration}
          error={!loading && !!configGeneration && !trimmedExtractionModel}
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
        title="Tender relevance configuration"
        description="Prompts, scoring rules and model settings used to classify and score tender relevance."
      >
        {relevanceLoading && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <CircularProgress size={20} />
            <Typography variant="body2" color="text.secondary">
              Loading relevance configuration...
            </Typography>
          </Box>
        )}
        {relevanceLoadError && (
          <Alert
            severity="error"
            action={
              <Button
                color="inherit"
                size="small"
                onClick={() => void loadRelevanceConfig()}
                disabled={relevanceLoading}
              >
                Reload
              </Button>
            }
            sx={reloadAlertSx}
          >
            {relevanceLoadError}
          </Alert>
        )}
        {relevanceSaveError && (
          <Alert
            severity={relevanceHasConflict ? 'warning' : 'error'}
            action={
              relevanceHasConflict ? (
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => void loadRelevanceConfig()}
                  disabled={relevanceLoading}
                >
                  Reload
                </Button>
              ) : undefined
            }
            sx={reloadAlertSx}
          >
            {relevanceSaveError}
          </Alert>
        )}
        {relevanceSaveSuccess && (
          <Alert severity="success" sx={{ mb: 2 }}>
            {relevanceSaveSuccess}
          </Alert>
        )}

        <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>
          Relevance prompts
        </Typography>
        <TextField
          label="Classification guidance"
          value={relevanceForm.classification_thoughts}
          onChange={(event) => handleRelevanceChange('classification_thoughts', event.target.value)}
          multiline
          minRows={6}
          fullWidth
          disabled={relevanceLoading || relevanceSaving || !configGeneration}
          error={!!displayedRelevanceErrors.classification_thoughts}
          helperText={displayedRelevanceErrors.classification_thoughts ?? 'Guidance used when assigning relevance tags.'}
          sx={{ mb: 2 }}
        />
        <TextField
          label="Focus-area taxonomy"
          value={relevanceForm.focus_areas}
          onChange={(event) => handleRelevanceChange('focus_areas', event.target.value)}
          multiline
          minRows={10}
          fullWidth
          disabled={relevanceLoading || relevanceSaving || !configGeneration}
          error={!!displayedRelevanceErrors.focus_areas}
          helperText={displayedRelevanceErrors.focus_areas ?? 'Define tags using the format: tag_id | Label.'}
          sx={{ mb: 2 }}
        />
        <TextField
          label="Work-type taxonomy"
          value={relevanceForm.work_types}
          onChange={(event) => handleRelevanceChange('work_types', event.target.value)}
          multiline
          minRows={10}
          fullWidth
          disabled={relevanceLoading || relevanceSaving || !configGeneration}
          error={!!displayedRelevanceErrors.work_types}
          helperText={displayedRelevanceErrors.work_types ?? 'Define tags using the format: tag_id | Label.'}
          sx={{ mb: 2 }}
        />
        <TextField
          label="Out-of-scope list"
          value={relevanceForm.out_of_scope}
          onChange={(event) => handleRelevanceChange('out_of_scope', event.target.value)}
          multiline
          minRows={6}
          fullWidth
          disabled={relevanceLoading || relevanceSaving || !configGeneration}
          error={!!displayedRelevanceErrors.out_of_scope}
          helperText={displayedRelevanceErrors.out_of_scope ?? 'Main deliverables that should be treated as out of scope.'}
          sx={{ mb: 3 }}
        />

        <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>
          Relevance scoring
        </Typography>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))' },
            gap: 2,
            mb: 3,
          }}
        >
          <TextField
            label="Focus area weight"
            type="number"
            value={relevanceForm.focus_area_weight}
            onChange={(event) => handleRelevanceChange('focus_area_weight', event.target.value)}
            disabled={relevanceLoading || relevanceSaving || !configGeneration}
            error={!!displayedRelevanceErrors.focus_area_weight}
            helperText={displayedRelevanceErrors.focus_area_weight ?? 'Value from 0 to 1.'}
            slotProps={{ htmlInput: { step: 'any' } }}
          />
          <TextField
            label="Work type weight"
            type="number"
            value={relevanceForm.work_type_weight}
            onChange={(event) => handleRelevanceChange('work_type_weight', event.target.value)}
            disabled={relevanceLoading || relevanceSaving || !configGeneration}
            error={!!displayedRelevanceErrors.work_type_weight}
            helperText={displayedRelevanceErrors.work_type_weight ?? 'Value from 0 to 1; weights must total 1.'}
            slotProps={{ htmlInput: { step: 'any' } }}
          />
          <TextField
            label="Recency weight"
            type="number"
            value={relevanceForm.recency_weight}
            onChange={(event) => handleRelevanceChange('recency_weight', event.target.value)}
            disabled={relevanceLoading || relevanceSaving || !configGeneration}
            error={!!displayedRelevanceErrors.recency_weight}
            helperText={displayedRelevanceErrors.recency_weight ?? 'Score adjustment reserved for recency.'}
            slotProps={{ htmlInput: { step: 'any' } }}
          />
          <TextField
            label="Recency horizon days"
            type="number"
            value={relevanceForm.recency_horizon_days}
            onChange={(event) => handleRelevanceChange('recency_horizon_days', event.target.value)}
            disabled={relevanceLoading || relevanceSaving || !configGeneration}
            error={!!displayedRelevanceErrors.recency_horizon_days}
            helperText={displayedRelevanceErrors.recency_horizon_days ?? 'Positive whole number of days.'}
            slotProps={{ htmlInput: { step: 1 } }}
          />
          <TextField
            label="Out-of-scope fit cap"
            type="number"
            value={relevanceForm.out_of_scope_fit_cap}
            onChange={(event) => handleRelevanceChange('out_of_scope_fit_cap', event.target.value)}
            disabled={relevanceLoading || relevanceSaving || !configGeneration}
            error={!!displayedRelevanceErrors.out_of_scope_fit_cap}
            helperText={displayedRelevanceErrors.out_of_scope_fit_cap ?? 'Whole number from 0 to 100.'}
            slotProps={{ htmlInput: { step: 1 } }}
          />
          <TextField
            label="Max focus areas"
            type="number"
            value={relevanceForm.max_focus_areas}
            onChange={(event) => handleRelevanceChange('max_focus_areas', event.target.value)}
            disabled={relevanceLoading || relevanceSaving || !configGeneration}
            error={!!displayedRelevanceErrors.max_focus_areas}
            helperText={displayedRelevanceErrors.max_focus_areas ?? 'Maximum focus-area tags retained.'}
            slotProps={{ htmlInput: { step: 1 } }}
          />
          <TextField
            label="Max work types"
            type="number"
            value={relevanceForm.max_work_types}
            onChange={(event) => handleRelevanceChange('max_work_types', event.target.value)}
            disabled={relevanceLoading || relevanceSaving || !configGeneration}
            error={!!displayedRelevanceErrors.max_work_types}
            helperText={displayedRelevanceErrors.max_work_types ?? 'Maximum work-type tags retained.'}
            slotProps={{ htmlInput: { step: 1 } }}
          />
        </Box>

        <Typography variant="subtitle1" sx={{ fontWeight: 700, mb: 1 }}>
          Relevance model settings
        </Typography>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', sm: 'minmax(0, 2fr) minmax(0, 1fr)' },
            gap: 2,
            mb: 2,
          }}
        >
          <TextField
            label="Relevance model"
            value={relevanceForm.relevance_model}
            onChange={(event) => handleRelevanceChange('relevance_model', event.target.value)}
            disabled={relevanceLoading || relevanceSaving || !configGeneration}
            error={!!displayedRelevanceErrors.relevance_model}
            helperText={displayedRelevanceErrors.relevance_model ?? 'Model used for relevance classification.'}
          />
          <TextField
            label="Temperature"
            type="number"
            value={relevanceForm.relevance_temperature}
            onChange={(event) => handleRelevanceChange('relevance_temperature', event.target.value)}
            disabled={relevanceLoading || relevanceSaving || !configGeneration}
            error={!!displayedRelevanceErrors.relevance_temperature}
            helperText={displayedRelevanceErrors.relevance_temperature ?? 'Value of 0 or greater.'}
            slotProps={{ htmlInput: { step: 'any' } }}
          />
        </Box>

        <Alert severity="info" sx={{ mb: 2 }}>
          Saved relevance settings affect future tender processing and do not automatically re-score existing tenders.
        </Alert>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button
            variant="contained"
            disabled={relevanceSaveDisabled}
            onClick={() => void handleRelevanceSave()}
            sx={{ width: { xs: '100%', sm: 'auto' }, minHeight: { xs: 44, sm: 36 } }}
          >
            {relevanceSaving ? <CircularProgress size={20} color="inherit" /> : 'Save relevance settings'}
          </Button>
        </Box>
      </ConfigurationSection>

      <ConfigurationSection
        title="Reprocess existing tenders"
        description="Re-runs the AI layer over every tender already in the database using the prompts saved above. Nothing is scraped."
      >
        <Alert severity="warning" sx={{ mb: 2 }}>
          This re-summarises and re-scores every stored tender, so it takes a long time and
          costs money. Scraped details such as titles, dates and contacts are left untouched;
          only the summary, relevance scores and search embedding are rewritten.
        </Alert>
        {reprocessError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {reprocessError}
          </Alert>
        )}
        {reprocessStarted && (
          <Alert severity="success" sx={{ mb: 2 }}>
            Reprocess run started. It continues in the background — you can close this page.
          </Alert>
        )}
        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button
            variant="contained"
            color="warning"
            onClick={() => {
              setReprocessError(null);
              setReprocessStarted(false);
              setReprocessConfirmOpen(true);
            }}
            disabled={reprocessStarting}
            sx={{ width: { xs: '100%', sm: 'auto' }, minHeight: { xs: 44, sm: 36 } }}
          >
            Reprocess all tenders
          </Button>
        </Box>
      </ConfigurationSection>

      <Dialog
        open={reprocessConfirmOpen}
        onClose={() => !reprocessStarting && setReprocessConfirmOpen(false)}
        aria-labelledby="reprocess-confirm-title"
      >
        <DialogTitle id="reprocess-confirm-title">Reprocess every tender?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Every tender in the database will be re-summarised and re-scored with the currently
            saved prompts. This can take a long time and incurs AI costs for each tender.
          </DialogContentText>
          <DialogContentText sx={{ mt: 2 }}>
            Titles, dates, values and contacts are not changed. The run happens in the
            background, so you do not need to stay on this page.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setReprocessConfirmOpen(false)} disabled={reprocessStarting}>
            Cancel
          </Button>
          <Button
            variant="contained"
            color="warning"
            onClick={() => void handleReprocessConfirm()}
            disabled={reprocessStarting}
          >
            {reprocessStarting ? 'Starting...' : 'Yes, reprocess everything'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
