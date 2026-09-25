import { useState, useEffect, type FormEvent } from 'react';
import { useSearchParams, Link as RouterLink } from 'react-router-dom';
import { verifyPasswordResetCode, confirmPasswordReset } from 'firebase/auth';
import {
  Box,
  Card,
  CardContent,
  Typography,
  TextField,
  Button,
  Alert,
  CircularProgress,
  Link,
} from '@mui/material';
import { auth } from '../lib/firebase';

type Status = 'verifying' | 'ready' | 'invalid' | 'submitting' | 'success';

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const oobCode = searchParams.get('oobCode');

  const [status, setStatus] = useState<Status>('verifying');
  const [email, setEmail] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);

  // Verify the code is real and not expired/used before showing the form —
  // this is what shows the email so the user knows which account they're
  // resetting, and stops us rendering a form for a dead link.
  useEffect(() => {
    if (!oobCode) {
      setStatus('invalid');
      return;
    }
    verifyPasswordResetCode(auth, oobCode)
      .then((verifiedEmail) => {
        setEmail(verifiedEmail);
        setStatus('ready');
      })
      .catch(() => {
        setStatus('invalid');
      });
  }, [oobCode]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    setStatus('submitting');
    try {
      await confirmPasswordReset(auth, oobCode!, password);
      setStatus('success');
    } catch (err: unknown) {
      console.error('Password reset failed:', err);
      setError('Something went wrong. This link may have expired — try requesting a new one.');
      setStatus('ready');
    }
  };

  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
        '@supports (height: 100dvh)': { minHeight: '100dvh' },
        bgcolor: 'background.default',
        boxSizing: 'border-box',
        p: { xs: 2, sm: 3 },
      }}
    >
      <Card
        sx={{
          width: '100%',
          maxWidth: 380,
          p: { xs: 0, sm: 1 },
          boxSizing: 'border-box',
          border: '1px solid',
          borderColor: 'secondary.main',
          boxShadow: (theme) => theme.palette.mode === 'light' ? '0 8px 24px rgba(36,45,50,0.10)' : 'none',
        }}
      >
        <CardContent>
          <Typography
            variant="h5"
            sx={{
              fontWeight: 700,
              mb: 3,
              textAlign: 'center',
              color: (theme) => theme.palette.mode === 'light' ? 'primary.main' : 'secondary.main',
            }}
          >
            Reset password
          </Typography>

          {status === 'verifying' && (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress />
            </Box>
          )}

          {status === 'invalid' && (
            <>
              <Alert severity="error" sx={{ mb: 2, overflowWrap: 'anywhere' }}>
                This link is invalid or has expired.
              </Alert>
              <Box sx={{ textAlign: 'center' }}>
                <Link component={RouterLink} to="/forgot-password">
                  Request a new link
                </Link>
              </Box>
            </>
          )}

          {status === 'success' && (
            <>
              <Alert severity="success" sx={{ mb: 2, overflowWrap: 'anywhere' }}>
                Your password has been updated.
              </Alert>
              <Box sx={{ textAlign: 'center' }}>
                <Link component={RouterLink} to="/login">
                  Back to login
                </Link>
              </Box>
            </>
          )}

          {(status === 'ready' || status === 'submitting') && (
            <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {error && <Alert severity="error" sx={{ overflowWrap: 'anywhere' }}>{error}</Alert>}

              {email && (
                <Typography variant="body2" color="text.secondary" sx={{ overflowWrap: 'anywhere' }}>
                  Setting a new password for <strong>{email}</strong>
                </Typography>
              )}

              <TextField
                label="New password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoFocus
                fullWidth
              />

              <TextField
                label="Confirm new password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                fullWidth
              />

              <Button
                type="submit"
                variant="contained"
                size="large"
                disabled={status === 'submitting'}
                fullWidth
              >
                {status === 'submitting' ? <CircularProgress size={24} color="inherit" /> : 'Update password'}
              </Button>
            </Box>
          )}
        </CardContent>
      </Card>
    </Box>
  );
}
