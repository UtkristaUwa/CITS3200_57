import { AppBar, Toolbar, Typography, Button, Box } from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import { signOut } from 'firebase/auth';
import { auth } from '../lib/firebase';
import { useAuth } from '../lib/AuthContext';

export default function TopNav() {
  const { isAdmin } = useAuth();

  return (
    <AppBar position="static" color="default" sx={{ mb: 3 }}>
      <Toolbar>
        <Typography
          variant="h6"
          component={RouterLink}
          to="/"
          sx={{ flexGrow: 1, textAlign: 'left', fontWeight: 700, textDecoration: 'none', color: 'inherit' }}
        >
          TenderAI
        </Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button color="inherit" component={RouterLink} to="/">
            Home
          </Button>
          <Button color="inherit" component={RouterLink} to="/favorites">
            Favorites
          </Button>
          {isAdmin && (
            <Button color="inherit" component={RouterLink} to="/admin">
              Admin
            </Button>
          )}
          <Button color="inherit" onClick={() => signOut(auth)}>
            Logout
          </Button>
        </Box>
      </Toolbar>
    </AppBar>
  );
}