import { useEffect, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  CircularProgress,
  Divider,
  FormControlLabel,
  Paper,
  Slider,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SendIcon from '@mui/icons-material/Send';
import SaveIcon from '@mui/icons-material/Save';
import {
  getTeamsAlertConfig,
  updateTeamsAlertConfig,
  testTeamsAlertWebhook,
  type TeamsConfigResponse,
} from '../lib/api';

const BRAND_COLORS = {
  blue: '#2D3AF1',
  lilac: '#CF9EFF',
  charcoal: '#242D32',
} as const;

export default function AlertsPage() {
  const [enabled, setEnabled] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState('');
  const [minFitScore, setMinFitScore] = useState(70);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [testSuccess, setTestSuccess] = useState<string | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    async function loadConfig() {
      setLoading(true);
      try {
        const data: TeamsConfigResponse = await getTeamsAlertConfig();
        if (isMounted) {
          setEnabled(data.enabled);
          setWebhookUrl(data.webhook_url);
          setMinFitScore(data.min_fit_score ?? 70);
          setUpdatedAt(data.updated_at);
        }
      } catch (err: unknown) {
        if (isMounted) {
          setSaveError(err instanceof Error ? err.message : 'Failed to load Teams alert configuration.');
        }
      } finally {
        if (isMounted) setLoading(false);
      }
    }
    loadConfig();
    return () => {
      isMounted = false;
    };
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setSaveSuccess(null);
    setSaveError(null);
    try {
      const res = await updateTeamsAlertConfig({
        enabled,
        webhook_url: webhookUrl.trim(),
        min_fit_score: minFitScore,
      });
      setSaveSuccess('Configuration saved successfully. Automated pipeline alerts are active.');
      setUpdatedAt(res.updated_at);
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save configuration.');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!webhookUrl.trim()) {
      setTestError('Please provide a Microsoft Teams Webhook URL to test.');
      return;
    }
    setTesting(true);
    setTestSuccess(null);
    setTestError(null);
    try {
      const res = await testTeamsAlertWebhook(webhookUrl.trim());
      setTestSuccess(res.message || 'Test card successfully posted to Microsoft Teams!');
    } catch (err: unknown) {
      setTestError(
        err instanceof Error
          ? err.message
          : 'Failed to deliver test message. Verify the Webhook URL and try again.'
      );
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ minWidth: 0, maxWidth: 900 }}>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 1 }}>
        Tender alerts (Microsoft Teams)
      </Typography>
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Automatically publish high-relevance tender opportunities directly into your organisation&apos;s Microsoft Teams channel.
      </Typography>

      {saveSuccess && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSaveSuccess(null)}>
          {saveSuccess}
        </Alert>
      )}

      {saveError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setSaveError(null)}>
          {saveError}
        </Alert>
      )}

      {testSuccess && (
        <Alert
          severity="success"
          sx={{ mb: 2 }}
          onClose={() => setTestSuccess(null)}
        >
          {testSuccess} Check your Teams channel to view the newly rendered test card!
        </Alert>
      )}

      {testError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setTestError(null)}>
          {testError}
        </Alert>
      )}

      <Paper component="section" sx={{ p: { xs: 2.5, sm: 3.5 }, mb: 3, borderRadius: 2 }}>
        <Typography variant="h6" sx={{ fontWeight: 600, mb: 2 }}>
          Webhook configuration
        </Typography>

        <Box sx={{ mb: 3 }}>
          <FormControlLabel
            control={
              <Switch
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                color="primary"
              />
            }
            label={
              <Box>
                <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                  Enable automated Teams alerts
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  When enabled, newly scraped tenders with Fit ≥ {minFitScore} will be sent to the configured Teams channel.
                </Typography>
              </Box>
            }
          />
        </Box>

        <Divider sx={{ my: 2.5 }} />

        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 0.5 }}>
            Teams Webhook URL
          </Typography>
          <TextField
            fullWidth
            size="small"
            placeholder="https://prod-xx.australiaeast.logic.azure.com:443/workflows/.../triggers/manual/paths/invoke?..."
            value={webhookUrl}
            onChange={(e) => setWebhookUrl(e.target.value)}
            helperText="The Workflow or Incoming Webhook URL generated from your Microsoft Teams channel."
            sx={{ mt: 0.5 }}
          />
        </Box>

        <Box sx={{ mb: 3.5 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
              Minimum AI Fit Threshold
            </Typography>
            <Typography variant="body2" sx={{ fontWeight: 700, color: BRAND_COLORS.blue }}>
              {minFitScore} / 100
            </Typography>
          </Box>
          <Slider
            value={minFitScore}
            min={0}
            max={100}
            step={5}
            marks={[
              { value: 0, label: 'All (0)' },
              { value: 50, label: '50' },
              { value: 70, label: '70 (Relevant)' },
              { value: 85, label: '85 (High)' },
              { value: 100, label: '100' },
            ]}
            onChange={(_, value) => setMinFitScore(value as number)}
            sx={{ color: BRAND_COLORS.blue }}
          />
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            Tenders scoring below {minFitScore} will be quietly skipped. If 1–3 qualifying tenders are ingested, separate detailed cards are posted. If 4+ qualify, a single consolidated digest card is sent.
          </Typography>
        </Box>

        {updatedAt && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
            Last updated: {new Date(updatedAt).toLocaleString()}
          </Typography>
        )}

        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1.5 }}>
          <Button
            variant="contained"
            color="primary"
            startIcon={saving ? <CircularProgress size={18} color="inherit" /> : <SaveIcon />}
            disabled={saving}
            onClick={handleSave}
            sx={{ fontWeight: 600 }}
          >
            {saving ? 'Saving...' : 'Save Configuration'}
          </Button>

          <Button
            variant="outlined"
            color="primary"
            startIcon={testing ? <CircularProgress size={18} color="inherit" /> : <SendIcon />}
            disabled={testing || !webhookUrl.trim()}
            onClick={handleTest}
            sx={{ fontWeight: 600 }}
          >
            {testing ? 'Sending Test...' : 'Test Webhook Connection'}
          </Button>
        </Box>
      </Paper>

      {/* Setup Guide Accordion */}
      <Accordion defaultExpanded sx={{ borderRadius: 2, overflow: 'hidden', mb: 3 }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            📘 How to set up Microsoft Teams Webhook in 3 minutes
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Box component="ol" sx={{ pl: 2.5, m: 0, '& li': { mb: 1.5 } }}>
            <li>
              <Typography variant="body2">
                <strong>Open Microsoft Teams</strong> and navigate to the channel where you want alerts (e.g. <em>#procurement</em> or <em>#tender-leads</em>).
              </Typography>
            </li>
            <li>
              <Typography variant="body2">
                Click the three dots (<strong>...</strong>) next to the channel name &rarr; select <strong>Workflows</strong>.
              </Typography>
            </li>
            <li>
              <Typography variant="body2">
                In the search box, type: <em>&ldquo;Post to a channel when a webhook request is received&rdquo;</em>.
              </Typography>
            </li>
            <li>
              <Typography variant="body2">
                Select the pre-built template, name it <strong>TenderAI Alerts</strong>, verify your channel, and click <strong>Create flow</strong>.
              </Typography>
            </li>
            <li>
              <Typography variant="body2">
                Copy the generated <strong>Webhook URL</strong>, paste it into the field above, and click <strong>Test Webhook Connection</strong>.
              </Typography>
            </li>
          </Box>
          <Alert severity="info" sx={{ mt: 2 }}>
            <strong>Note for Entire Team:</strong> You only need to set this up once per channel! Every employee who is a member of the Teams channel will automatically see the new tender alerts.
          </Alert>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}
