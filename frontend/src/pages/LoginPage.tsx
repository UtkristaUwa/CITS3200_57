import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { signInWithEmailAndPassword } from 'firebase/auth';
import {
  Box,
  Card,
  CardContent,
  Typography,
  TextField,
  Button,
  Alert,
  CircularProgress,
} from '@mui/material';
import { auth } from '../lib/firebase';
import { Link as RouterLink } from 'react-router-dom';
import { Link } from '@mui/material';

export default function LoginPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

          <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {error && <Alert severity="error" sx={{ overflowWrap: 'anywhere' }}>{error}</Alert>}

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

            <Button type="submit" variant="contained" size="large" disabled={loading} fullWidth>
              {loading ? <CircularProgress size={24} color="inherit" /> : 'Log in'}
            </Button>
          </Box>
        </CardContent>
      </Card>
    </Box>
  );
}
