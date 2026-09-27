import { useState, type MouseEvent } from 'react';
import { AppBar, Toolbar, Typography, Button, Box, IconButton, Menu, MenuItem, Switch, styled } from '@mui/material';
import MenuIcon from '@mui/icons-material/Menu';
import { Link as RouterLink, useLocation } from 'react-router-dom';
import { signOut } from 'firebase/auth';
import LightModeRoundedIcon from '@mui/icons-material/LightModeRounded';
import DarkModeRoundedIcon from '@mui/icons-material/DarkModeRounded';
import { auth } from '../lib/firebase';
import { useAuth } from '../lib/AuthContext';
import { useColorMode } from '../lib/ThemeContext';

const BRAND_COLORS = {
  blue: '#2D3AF1',
  lilac: '#CF9EFF',
  orange: '#FF7C00',
  charcoal: '#242D32',
  white: '#FFFFFF',
} as const;

const ModeSwitch = styled(Switch)(({ theme }) => ({
  width: 74,
  height: 42,
  padding: 8,
  '& .MuiSwitch-switchBase': {
    margin: 2,
    padding: 0,
    transitionDuration: '200ms',
    transform: 'translateX(10px)',
    '&.Mui-checked': {
      transform: 'translateX(35px)',
      color: BRAND_COLORS.white,
      '& + .MuiSwitch-track': {
        backgroundColor: BRAND_COLORS.blue,
        opacity: 1,
      },
    },
  },
  '& .MuiSwitch-thumb': {
    boxShadow: 'none',
    width: 34,
    height: 34,
    backgroundColor: theme.palette.mode === 'dark' ? BRAND_COLORS.charcoal : BRAND_COLORS.orange,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  '& .MuiSwitch-track': {
    borderRadius: 21,
    backgroundColor: BRAND_COLORS.lilac,
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
        icon={<LightModeRoundedIcon sx={{ fontSize: 22, color: BRAND_COLORS.charcoal, p: '1px', boxSizing: 'border-box', position: 'relative', top: '7.5px' }} />}
        checkedIcon={<DarkModeRoundedIcon sx={{ fontSize: 22, color: BRAND_COLORS.white, p: '1px', boxSizing: 'border-box', position: 'relative', top: '7.5px' }} />}
        slotProps={{ input: { 'aria-label': 'Toggle dark mode' } }}
      />
    </Box>
  );
}

export default function TopNav() {
  const { isAdmin } = useAuth();
  const location = useLocation();
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);

  const handleMenuOpen = (event: MouseEvent<HTMLElement>) => {
    setMenuAnchor(event.currentTarget);
  };

  const handleMenuClose = () => {
    setMenuAnchor(null);
  };

  const handleLogout = () => {
    handleMenuClose();
    void signOut(auth);
  };

  return (
    <AppBar position="static" color="default" sx={{ mb: 3 }}>
      <Toolbar>
        <Typography
          variant="h6"
          component={RouterLink}
          to="/"
          sx={{
            flexGrow: 1,
            textAlign: 'left',
            fontWeight: 800,
            textDecoration: 'none',
            color: (theme) => theme.palette.mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.white,
          }}
        >
          TenderAI
        </Typography>
        <ModeToggle />
        <Box sx={{ display: { xs: 'none', sm: 'flex' }, gap: 1, alignItems: 'center' }}>
          <Button
            color={location.pathname === '/' ? 'primary' : 'inherit'}
            sx={{
              fontWeight: location.pathname === '/' ? 700 : 500,
              color: (theme) => location.pathname === '/'
                ? (theme.palette.mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.lilac)
                : 'text.primary',
              borderBottom: location.pathname === '/' ? `3px solid ${BRAND_COLORS.blue}` : '3px solid transparent',
              borderRadius: 0,
            }}
            component={RouterLink}
            to="/"
          >
            Home
          </Button>
          <Button
            color={location.pathname === '/favorites' ? 'primary' : 'inherit'}
            sx={{
              fontWeight: location.pathname === '/favorites' ? 700 : 500,
              color: (theme) => location.pathname === '/favorites'
                ? (theme.palette.mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.lilac)
                : 'text.primary',
              borderBottom: location.pathname === '/favorites' ? `3px solid ${BRAND_COLORS.blue}` : '3px solid transparent',
              borderRadius: 0,
            }}
            component={RouterLink} 
            to="/favorites"
          >
            Favorites
          </Button>
          {isAdmin && (
            <Button 
              color={location.pathname.startsWith('/admin') ? 'primary' : 'inherit'}
              sx={{
                fontWeight: location.pathname.startsWith('/admin') ? 700 : 500,
                color: (theme) => location.pathname.startsWith('/admin')
                  ? (theme.palette.mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.lilac)
                  : 'text.primary',
                borderBottom: location.pathname.startsWith('/admin') ? `3px solid ${BRAND_COLORS.blue}` : '3px solid transparent',
                borderRadius: 0,
              }}
              component={RouterLink} 
              to="/admin"
            >
              Admin
            </Button>
          )}
          <Button color="inherit" onClick={() => void signOut(auth)}>
            Logout
          </Button>
        </Box>

        <IconButton
          color="inherit"
          aria-label="Open navigation menu"
          aria-controls={menuAnchor ? 'mobile-navigation-menu' : undefined}
          aria-haspopup="true"
          aria-expanded={menuAnchor ? 'true' : undefined}
          onClick={handleMenuOpen}
          sx={{ display: { xs: 'inline-flex', sm: 'none' }, minWidth: 48, minHeight: 48 }}
        >
          <MenuIcon />
        </IconButton>

        <Menu
          id="mobile-navigation-menu"
          anchorEl={menuAnchor}
          open={Boolean(menuAnchor)}
          onClose={handleMenuClose}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
          transformOrigin={{ vertical: 'top', horizontal: 'right' }}
          slotProps={{ paper: { sx: { minWidth: 180 } } }}
        >
          <MenuItem
            component={RouterLink}
            to="/"
            selected={location.pathname === '/'}
            onClick={handleMenuClose}
          >
            Home
          </MenuItem>
          <MenuItem
            component={RouterLink}
            to="/favorites"
            selected={location.pathname === '/favorites'}
            onClick={handleMenuClose}
          >
            Favorites
          </MenuItem>
          {isAdmin && (
            <MenuItem
              component={RouterLink}
              to="/admin"
              selected={location.pathname.startsWith('/admin')}
              onClick={handleMenuClose}
            >
              Admin
            </MenuItem>
          )}
          <MenuItem onClick={handleLogout}>Logout</MenuItem>
        </Menu>
      </Toolbar>
    </AppBar>
  );
}
