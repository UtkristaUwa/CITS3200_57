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
      await sendPasswordResetEmail(auth, email);
    } catch (err: unknown) {
      // Deliberately ignore auth/user-not-found — always show the same
      // success state whether or not the email exists. Same reasoning as
      // the login page: don't let this form be used to check who has an
      // account on an invite-only system.
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
        bgcolor: '#fcfcfc',
      }}
    >
      <Card sx={{ width: 380, p: 1 }}>
        <CardContent>
          <Typography variant="h5" sx={{ fontWeight: 700, mb: 3, textAlign: 'center' }}>
            Reset password
          </Typography>

          {sent ? (
            <>
              <Alert severity="success" sx={{ mb: 2 }}>
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
              {error && <Alert severity="error">{error}</Alert>}

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