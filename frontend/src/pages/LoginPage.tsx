import { useState, useEffect, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  signInWithEmailAndPassword,
  signInWithPopup,
  signInWithRedirect,
  getRedirectResult,
  type AuthError,
} from 'firebase/auth';
import {
  Box,
  Card,
  CardContent,
  Typography,
  TextField,
  Button,
  Alert,
  CircularProgress,
  Divider,
} from '@mui/material';
import { auth, microsoftProvider } from '../lib/firebase';
import { Link as RouterLink } from 'react-router-dom';
import { Link } from '@mui/material';

/**
 * Firebase throws errors with a `.code`. Only the ones a user can actually
 * act on get their own message — everything else stays generic so we never
 * leak whether an account exists.
 */
function ssoErrorMessage(code: string): string {
  switch (code) {
    case 'auth/account-exists-with-different-credential':
      return 'That email already has a TenderAI password account. Sign in with your email and password instead.';
    case 'auth/operation-not-allowed':
      return 'Microsoft sign-in is not enabled for this project yet. Ask an admin to finish the setup in the Firebase console.';
    case 'auth/unauthorized-domain':
      return 'This site is not on the list of domains approved for sign-in. Ask an admin to add it in Firebase Authentication settings.';
    case 'auth/user-disabled':
      return 'This account has been disabled.';
    default:
      return 'Microsoft sign-in failed. Please try again, or use your email and password.';
  }
}

// The popup is the better experience, but some browsers and most embedded
// webviews block it outright. In those cases fall back to a full redirect,
// which always works at the cost of leaving the page.
const POPUP_UNAVAILABLE = new Set([
  'auth/popup-blocked',
  'auth/operation-not-supported-in-this-environment',
  'auth/web-storage-unsupported',
]);

// The user closing the popup, or opening a second one, is not an error worth
// showing them.
const POPUP_DISMISSED = new Set(['auth/popup-closed-by-user', 'auth/cancelled-popup-request']);

export default function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [ssoLoading, setSsoLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If we fell back to a redirect, we come back to this page with the result
  // waiting. A success needs no handling here — onAuthStateChanged in
  // AuthContext fires and RedirectIfAuthed moves us on — but a failure would
  // otherwise vanish silently.
  useEffect(() => {
    getRedirectResult(auth).catch((err: AuthError) => {
      setError(ssoErrorMessage(err.code));
      console.error('Microsoft redirect sign-in failed:', err);
    });
  }, []);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      await signInWithEmailAndPassword(auth, email, password);
      navigate('/');
    } catch (err: unknown) {
      // Firebase throws errors with a `.code` like 'auth/invalid-credential','auth/user-not-found', 'auth/wrong-password', 'auth/too-many-requests'.
      // Keeping this error message generic means we don't reveal whether the email exists or the password was wrong specifically for security reasons.
      setError('Incorrect email or password. Please try again.');
      console.error('Login failed:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleMicrosoftLogin = async () => {
    setError(null);
    setSsoLoading(true);

    try {
      await signInWithPopup(auth, microsoftProvider());
      navigate('/');
    } catch (err: unknown) {
      const code = (err as AuthError).code ?? '';

      if (POPUP_UNAVAILABLE.has(code)) {
        // Leaves the page entirely; getRedirectResult above picks up the
        // outcome when the browser comes back.
        await signInWithRedirect(auth, microsoftProvider());
        return;
      }
      if (!POPUP_DISMISSED.has(code)) {
        setError(ssoErrorMessage(code));
        console.error('Microsoft sign-in failed:', err);
      }
    } finally {
      setSsoLoading(false);
    }
  };

  const busy = loading || ssoLoading;

  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
        '@supports (height: 100dvh)': { minHeight: '100dvh' },
        bgcolor: '#fcfcfc',
        boxSizing: 'border-box',
        p: { xs: 2, sm: 3 },
      }}
    >
      <Card sx={{ width: '100%', maxWidth: 380, p: { xs: 0, sm: 1 }, boxSizing: 'border-box' }}>
        <CardContent>
          <Typography variant="h5" sx={{ fontWeight: 700, mb: 3, textAlign: 'center' }}>
            TenderAI
          </Typography>

          {error && <Alert severity="error" sx={{ mb: 2, overflowWrap: 'anywhere' }}>{error}</Alert>}

          <Button
            variant="outlined"
            size="large"
            fullWidth
            disabled={busy}
            onClick={handleMicrosoftLogin}
            sx={{ textTransform: 'none' }}
          >
            {ssoLoading ? <CircularProgress size={24} /> : 'Sign in with Microsoft'}
          </Button>

          <Divider sx={{ my: 3 }}>or</Divider>

          <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <TextField
              label="Email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
              fullWidth
            />

            <TextField
              label="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              fullWidth
            />

            <Box sx={{ textAlign: 'right' }}>
                <Link component={RouterLink} to="/forgot-password" variant="body2">Forgot password?</Link>
            </Box>

            <Button type="submit" variant="contained" size="large" disabled={busy} fullWidth>
              {loading ? <CircularProgress size={24} color="inherit" /> : 'Log in'}
            </Button>
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
}
