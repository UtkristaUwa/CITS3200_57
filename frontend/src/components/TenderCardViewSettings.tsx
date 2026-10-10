import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  FormGroup,
  FormLabel,
  IconButton,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useAuth } from '../lib/AuthContext';
import { useUserPreferences } from '../lib/UserPreferencesContext';
import {
  CONFIGURABLE_CARD_FIELDS,
  DEFAULT_COMPACT_CARD_FIELDS,
  type CardFieldId,
} from '../lib/tenderPreferences';

export interface TenderCardViewSettingsProps {
  open: boolean;
  onClose: () => void;
}

interface DraftSelection {
  ownerUid: string;
  fields: CardFieldId[];
  baselineFields: CardFieldId[];
  externalChangeDetected: boolean;
}

function orderFields(fields: readonly CardFieldId[]): CardFieldId[] {
  const selectedFields = new Set(fields);
  return CONFIGURABLE_CARD_FIELDS
    .map((field) => field.id)
    .filter((field) => selectedFields.has(field));
}

function selectionsMatch(
  firstSelection: readonly CardFieldId[],
  secondSelection: readonly CardFieldId[],
): boolean {
  return firstSelection.length === secondSelection.length
    && firstSelection.every((field, index) => field === secondSelection[index]);
}

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : 'Failed to save tender card view settings.';
}

export function TenderCardViewSettings({
  open,
  onClose,
}: TenderCardViewSettingsProps) {
  const dialogTitleId = useId();
  const dialogDescriptionId = useId();
  const { user, loading: authLoading } = useAuth();
  const {
    preferences,
    ready,
    loading: preferencesLoading,
    saving: preferencesSaving,
    error: preferencesError,
    saveCardViewPreferences,
  } = useUserPreferences();
  const currentUid = user?.uid ?? null;
  const orderedSavedFields = useMemo(
    () => orderFields(preferences.cardView.compactFields),
    [preferences.cardView.compactFields],
  );
  const [draftSelection, setDraftSelection] = useState<DraftSelection | null>(null);
  const [localSaving, setLocalSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const mountedRef = useRef(false);
  const requestTokenRef = useRef(0);
  const latestDialogIdentityRef = useRef({ open, uid: currentUid });
  latestDialogIdentityRef.current = { open, uid: currentUid };

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestTokenRef.current += 1;
    };
  }, []);

  useEffect(() => {
    requestTokenRef.current += 1;
    setLocalSaving(false);
    setSaveError(null);
    setDraftSelection((currentDraft) => {
      if (!open || currentDraft?.ownerUid !== currentUid) return null;
      return currentDraft;
    });
  }, [currentUid, open]);

  useEffect(() => {
    if (
      !open
      || authLoading
      || preferencesLoading
      || !ready
      || !currentUid
    ) {
      return;
    }

    setDraftSelection((currentDraft) => {
      if (!currentDraft || currentDraft.ownerUid !== currentUid) {
        if (preferencesError) return null;
        return {
          ownerUid: currentUid,
          fields: [...orderedSavedFields],
          baselineFields: [...orderedSavedFields],
          externalChangeDetected: false,
        };
      }

      if (selectionsMatch(currentDraft.baselineFields, orderedSavedFields)) {
        return currentDraft;
      }

      const draftWasDirty = !selectionsMatch(
        currentDraft.fields,
        currentDraft.baselineFields,
      );
      const saveAppliedCurrentDraft = localSaving
        && selectionsMatch(currentDraft.fields, orderedSavedFields);

      if (!draftWasDirty || saveAppliedCurrentDraft) {
        return {
          ...currentDraft,
          fields: [...orderedSavedFields],
          baselineFields: [...orderedSavedFields],
          externalChangeDetected: false,
        };
      }

      return {
        ...currentDraft,
        baselineFields: [...orderedSavedFields],
        externalChangeDetected: true,
      };
    });
  }, [
    authLoading,
    currentUid,
    localSaving,
    open,
    orderedSavedFields,
    preferencesError,
    preferencesLoading,
    ready,
  ]);

  const activeDraft =
    currentUid && draftSelection?.ownerUid === currentUid
      ? draftSelection
      : null;
  const selectedFieldIds = useMemo(
    () => new Set(activeDraft?.fields ?? []),
    [activeDraft?.fields],
  );
  const draftIsDirty = activeDraft
    ? !selectionsMatch(activeDraft.fields, activeDraft.baselineFields)
    : false;
  const preferencesAvailable =
    !authLoading
    && !preferencesLoading
    && ready
    && currentUid !== null
    && activeDraft !== null;
  const saveDisabled =
    !preferencesAvailable
    || !draftIsDirty
    || localSaving
    || preferencesSaving;

  const handleFieldChange = (fieldId: CardFieldId, checked: boolean) => {
    if (!currentUid || localSaving) return;

    setDraftSelection((currentDraft) => {
      if (!currentDraft || currentDraft.ownerUid !== currentUid) return currentDraft;

      const nextSelectedFields = new Set(currentDraft.fields);
      if (checked) {
        nextSelectedFields.add(fieldId);
      } else {
        nextSelectedFields.delete(fieldId);
      }
      const nextFields = orderFields([...nextSelectedFields]);

      return {
        ...currentDraft,
        fields: nextFields,
        externalChangeDetected: currentDraft.externalChangeDetected
          && !selectionsMatch(nextFields, currentDraft.baselineFields),
      };
    });
    setSaveError(null);
  };

  const handleReset = () => {
    if (!currentUid || localSaving) return;

    setDraftSelection((currentDraft) => {
      if (!currentDraft || currentDraft.ownerUid !== currentUid) return currentDraft;

      const defaultFields = orderFields(DEFAULT_COMPACT_CARD_FIELDS);
      return {
        ...currentDraft,
        fields: defaultFields,
        externalChangeDetected: currentDraft.externalChangeDetected
          && !selectionsMatch(defaultFields, currentDraft.baselineFields),
      };
    });
    setSaveError(null);
  };

  const handleCancel = () => {
    if (localSaving) return;

    requestTokenRef.current += 1;
    setDraftSelection(null);
    setSaveError(null);
    onClose();
  };

  const handleSave = async () => {
    if (
      !currentUid
      || !activeDraft
      || activeDraft.ownerUid !== currentUid
      || !preferencesAvailable
      || !draftIsDirty
      || localSaving
      || preferencesSaving
    ) {
      return;
    }

    const saveUid = currentUid;
    const compactFields = orderFields(activeDraft.fields);
    const requestToken = requestTokenRef.current + 1;
    requestTokenRef.current = requestToken;
    setLocalSaving(true);
    setSaveError(null);

    try {
      await saveCardViewPreferences({ compactFields });

      const latestIdentity = latestDialogIdentityRef.current;
      if (
        !mountedRef.current
        || requestTokenRef.current !== requestToken
        || !latestIdentity.open
        || latestIdentity.uid !== saveUid
      ) {
        return;
      }

      setLocalSaving(false);
      setDraftSelection(null);
      onClose();
    } catch (error: unknown) {
      const latestIdentity = latestDialogIdentityRef.current;
      if (
        !mountedRef.current
        || requestTokenRef.current !== requestToken
        || !latestIdentity.open
        || latestIdentity.uid !== saveUid
      ) {
        return;
      }

      setLocalSaving(false);
      setSaveError(errorMessage(error));
    }
  };

  const showLoading = authLoading
    || (currentUid !== null && (preferencesLoading || !ready))
    || (
      currentUid !== null
      && !preferencesError
      && !activeDraft
    );
  const initialLoadFailed =
    currentUid !== null
    && !authLoading
    && !preferencesLoading
    && ready
    && preferencesError !== null
    && activeDraft === null;

  return (
    <Dialog
      open={open}
      onClose={(_event, reason) => {
        if (localSaving && (reason === 'backdropClick' || reason === 'escapeKeyDown')) {
          return;
        }
        handleCancel();
      }}
      fullWidth
      maxWidth="sm"
      aria-labelledby={dialogTitleId}
      aria-describedby={dialogDescriptionId}
      slotProps={{
        paper: {
          sx: {
            m: { xs: 2, sm: 4 },
            width: { xs: 'calc(100% - 32px)', sm: '100%' },
            maxHeight: { xs: 'calc(100% - 32px)', sm: 'calc(100% - 64px)' },
          },
        },
      }}
    >
      <DialogTitle id={dialogTitleId} sx={{ pr: 7 }}>
        Tender card view settings
        <IconButton
          aria-label="Close tender card view settings"
          onClick={handleCancel}
          disabled={localSaving}
          sx={{
            position: 'absolute',
            right: 8,
            top: 8,
            minWidth: 44,
            minHeight: 44,
          }}
        >
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers sx={{ overflowX: 'hidden' }}>
        <Typography id={dialogDescriptionId} variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Choose which fields appear on the minimized tender card. Other fields will still be available in View More.
        </Typography>

        {showLoading && (
          <Box
            role="status"
            aria-live="polite"
            sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1.5, py: 5 }}
          >
            <CircularProgress size={24} />
            <Typography variant="body2">Loading view settings...</Typography>
          </Box>
        )}

        {!showLoading && currentUid === null && (
          <Alert severity="info">
            Sign in to configure your tender card view.
          </Alert>
        )}

        {!showLoading && initialLoadFailed && (
          <Alert severity="error">
            {preferencesError.message || 'Failed to load your saved view settings.'}
          </Alert>
        )}

        {!showLoading && activeDraft && (
          <>
            {activeDraft.externalChangeDetected && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                Your saved settings changed while this dialog was open. Your unsaved selections have been kept; saving will replace the newer card selection.
              </Alert>
            )}

            {(saveError || preferencesError) && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {saveError ?? preferencesError?.message}
              </Alert>
            )}

            <FormControl
              component="fieldset"
              variant="standard"
              fullWidth
              sx={{ m: 0, p: 0, minWidth: 0, border: 0 }}
            >
              <FormLabel component="legend" sx={{ mb: 0.5 }}>
                Fields shown when minimized
              </FormLabel>
              <FormGroup>
                {CONFIGURABLE_CARD_FIELDS.map((field) => (
                  <FormControlLabel
                    key={field.id}
                    disabled={localSaving}
                    control={(
                      <Checkbox
                        checked={selectedFieldIds.has(field.id)}
                        onChange={(event) => handleFieldChange(field.id, event.target.checked)}
                      />
                    )}
                    label={field.label}
                    sx={{ minHeight: 44, mx: 0 }}
                  />
                ))}
              </FormGroup>
            </FormControl>
          </>
        )}
      </DialogContent>

      <DialogActions
        sx={{
          px: { xs: 2, sm: 3 },
          py: 2,
          gap: 1,
          alignItems: { xs: 'stretch', sm: 'center' },
          flexDirection: { xs: 'column', sm: 'row' },
        }}
      >
        <Button
          type="button"
          onClick={handleReset}
          disabled={!activeDraft || localSaving}
          sx={{ mr: { sm: 'auto' } }}
        >
          Reset to Default
        </Button>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1 }}>
          <Button
            type="button"
            onClick={handleCancel}
            disabled={localSaving}
            sx={{ flex: { xs: 1, sm: 'initial' } }}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="contained"
            onClick={() => void handleSave()}
            disabled={saveDisabled}
            sx={{ flex: { xs: 1, sm: 'initial' }, minWidth: 100 }}
          >
            {localSaving ? (
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 1 }}>
                <CircularProgress size={18} color="inherit" aria-hidden="true" />
                Saving...
              </Box>
            ) : (
              'Save'
            )}
          </Button>
        </Box>
      </DialogActions>
    </Dialog>
  );
}
