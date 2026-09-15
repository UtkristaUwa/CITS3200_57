import { AppBar, Toolbar, Typography, Button, Box } from '@mui/material';
import { Link as RouterLink, useLocation } from 'react-router-dom';
import { signOut } from 'firebase/auth';
import { auth } from '../lib/firebase';
import { useAuth } from '../lib/AuthContext';

export default function TopNav() {
  const { isAdmin } = useAuth();
  const location = useLocation(); 

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
          <Button 
            color={location.pathname === '/' ? 'primary' : 'inherit'}
            sx={{ fontWeight: location.pathname === '/' ? 700 : 400 }}
            component={RouterLink} 
            to="/"
          >
            Home
          </Button>
          <Button 
            color={location.pathname === '/favorites' ? 'primary' : 'inherit'}
            sx={{ fontWeight: location.pathname === '/favorites' ? 700 : 400 }}
            component={RouterLink} 
            to="/favorites"
          >
            Favorites
          </Button>
          {isAdmin && (
            <Button 
              color={location.pathname.startsWith('/admin') ? 'primary' : 'inherit'}
              sx={{ fontWeight: location.pathname.startsWith('/admin') ? 700 : 400 }}
              component={RouterLink} 
              to="/admin"
            >
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