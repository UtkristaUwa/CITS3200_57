import { AppBar, Toolbar, Typography, Button, Box, Switch, styled } from '@mui/material';
import { Link as RouterLink, useLocation } from 'react-router-dom';
import { signOut } from 'firebase/auth';
import LightModeRoundedIcon from '@mui/icons-material/LightModeRounded';
import DarkModeRoundedIcon from '@mui/icons-material/DarkModeRounded';
import { auth } from '../lib/firebase';
import { useAuth } from '../lib/AuthContext';
import { useColorMode } from '../lib/ThemeContext';

const ModeSwitch = styled(Switch)(({ theme }) => ({
  width: 58,
  height: 32,
  padding: 6,
  '& .MuiSwitch-switchBase': {
    margin: 1,
    padding: 0,
    transitionDuration: '200ms',
    transform: 'translateX(4px)',
    '&.Mui-checked': {
      transform: 'translateX(26px)',
      color: '#fff',
      '& + .MuiSwitch-track': {
        backgroundColor: '#1f1f1f',
        opacity: 1,
      },
    },
  },
  '& .MuiSwitch-thumb': {
    boxShadow: 'none',
    width: 26,
    height: 26,
    backgroundColor: theme.palette.mode === 'dark' ? '#1f1f1f' : '#fdd835',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  '& .MuiSwitch-track': {
    borderRadius: 16,
    backgroundColor: '#aab4be',
    opacity: 1,
  },
}));

function ModeToggle() {
  const { mode, toggleColorMode } = useColorMode();
  return (
    <Box sx={{ display: 'flex', alignItems: 'center' }}>
      <ModeSwitch
        checked={mode === 'dark'}
        onChange={toggleColorMode}
        icon={<LightModeRoundedIcon sx={{ fontSize: 22, color: '#5f4b00', p: '2px', boxSizing: 'border-box', transform: 'translateY(4px)' }} />}
        checkedIcon={<DarkModeRoundedIcon sx={{ fontSize: 22, color: '#fff', p: '2px', boxSizing: 'border-box', transform: 'translateY(4px)' }} />}
        inputProps={{ 'aria-label': 'Toggle dark mode' }}
      />
    </Box>
  );
}

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
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <Button
            color={location.pathname === '/' ? 'primary' : 'inherit'}
            sx={{ fontWeight: location.pathname === '/' ? 700 : 500 }}
            component={RouterLink}
            to="/"
          >
            Home
          </Button>
          <ModeToggle />
          <Button
            color={location.pathname === '/favorites' ? 'primary' : 'inherit'}
            sx={{ fontWeight: location.pathname === '/favorites' ? 700 : 500 }}
            component={RouterLink} 
            to="/favorites"
          >
            Favorites
          </Button>
          {isAdmin && (
            <Button 
              color={location.pathname.startsWith('/admin') ? 'primary' : 'inherit'}
              sx={{ fontWeight: location.pathname.startsWith('/admin') ? 700 : 500 }}
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