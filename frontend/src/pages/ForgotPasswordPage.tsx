import { useState, type FormEvent } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import { sendPasswordResetEmail } from 'firebase/auth';
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

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
        await sendPasswordResetEmail(auth, email, {
          url: `${window.location.origin}/reset-password`,
          handleCodeInApp: false,
        });
    } catch (err: unknown) {
      // Chosen to ignore auth/user-not-found because we want it to always show the same success state whether or not the email exists for security reasons
      console.error('Password reset request failed:', err);
    } finally {
      setLoading(false);
      setSent(true);
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

          {sent ? (
            <>
              <Alert severity="success" sx={{ mb: 2, overflowWrap: 'anywhere' }}>
                If an account exists for that email, a password reset link has been sent.
                Check your inbox.
              </Alert>
              <Box sx={{ textAlign: 'center' }}>
                <Link component={RouterLink} to="/login">
                  Back to login
                </Link>
              </Box>
            </>
          ) : (
            <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {error && <Alert severity="error" sx={{ overflowWrap: 'anywhere' }}>{error}</Alert>}

              <Typography variant="body2" color="text.secondary">
                Enter your email and we'll send you a link to reset your password.
              </Typography>

              <TextField
                label="Email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
                fullWidth
              />

              <Button type="submit" variant="contained" size="large" disabled={loading} fullWidth>
                {loading ? <CircularProgress size={24} color="inherit" /> : 'Send reset link'}
              </Button>

              <Box sx={{ textAlign: 'center' }}>
                <Link component={RouterLink} to="/login">
                  Back to login
                </Link>
              </Box>
            </Box>
          )}
        </CardContent>
      </Card>
    </Box>
  );
}
