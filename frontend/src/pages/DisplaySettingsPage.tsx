import { useState } from 'react';
import axios from 'axios';
import {
  Alert,
  Box,
  CircularProgress,
  FormControlLabel,
  Paper,
  Switch,
  Typography,
} from '@mui/material';
import { updateDisplaySettings, type DisplaySettings } from '../lib/api';
import { useDisplaySettings } from '../lib/DisplaySettingsContext';

export default function DisplaySettingsPage() {
  const { settings, loading, setSettings } = useDisplaySettings();
  const [saving, setSaving] = useState<keyof DisplaySettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async (key: keyof DisplaySettings, value: boolean) => {
    const next = { ...settings, [key]: value };
    // Applied straight away so the switch doesn't lag behind the click; rolled
    // back below if the save fails.
    setSettings(next);
    setSaving(key);
    setError(null);

    try {
      setSettings(await updateDisplaySettings(next));
    } catch (err) {
      setSettings(settings);
      const status = axios.isAxiosError(err) ? err.response?.status : undefined;
      if (status === 401) {
        setError('Your session has expired. Please sign in again.');
      } else if (status === 403) {
        setError('You do not have permission to change display settings.');
      } else if (status === 503) {
        setError('Display settings are currently unavailable. Nothing was changed.');
      } else {
        setError('Unable to save the setting. Please try again.');
      }
    } finally {
      setSaving(null);
    }
  };

  return (
    <Box sx={{ width: '100%', maxWidth: 1000, minWidth: 0 }}>
      <Typography
        variant="h5"
        sx={{
          fontWeight: 700,
          mb: 1,
          color: (theme) => (theme.palette.mode === 'light' ? 'primary.main' : 'secondary.main'),
        }}
      >
        Display settings
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        These apply to every admin, not just you, and change what admins see. Staff always have
        low-scoring tenders hidden, whatever is set here.
      </Typography>

      <Paper
        component="section"
        sx={{
          p: { xs: 2, sm: 3 },
          minWidth: 0,
          bgcolor: 'background.paper',
          border: '1px solid',
          borderColor: 'secondary.main',
          borderLeft: '4px solid',
          borderLeftColor: 'primary.main',
        }}
      >
        {loading && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <CircularProgress size={20} />
            <Typography variant="body2" color="text.secondary">
              Loading settings...
            </Typography>
          </Box>
        )}
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        <FormControlLabel
          control={
            <Switch
              checked={settings.show_json}
              onChange={(event) => void toggle('show_json', event.target.checked)}
              disabled={loading || saving !== null}
            />
          }
          label="Show JSON"
        />
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3, ml: 6 }}>
          Adds each tender's complete raw record to the bottom of its card. Includes the full
          extracted text of every attachment, so cards for large tenders get very long.
        </Typography>

        <FormControlLabel
          control={
            <Switch
              checked={settings.show_irrelevant}
              onChange={(event) => void toggle('show_irrelevant', event.target.checked)}
              disabled={loading || saving !== null}
            />
          }
          label="Show irrelevant"
        />
        <Typography variant="body2" color="text.secondary" sx={{ ml: 6 }}>
          Shows every tender regardless of its fit score, tinted to mark the low scorers. Tenders
          that were never scored are tinted too. With this off, admins see the same tenders staff
          do.
        </Typography>
      </Paper>
    </Box>
  );
}
