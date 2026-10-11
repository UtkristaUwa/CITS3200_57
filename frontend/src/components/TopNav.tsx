import { useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { AppBar, Toolbar, Typography, Button, Box, IconButton, Menu, MenuItem, Switch, Tooltip, styled } from '@mui/material';
import MenuIcon from '@mui/icons-material/Menu';
import { Link as RouterLink, useLocation } from 'react-router-dom';
import { signOut } from 'firebase/auth';
import LightModeRoundedIcon from '@mui/icons-material/LightModeRounded';
import DarkModeRoundedIcon from '@mui/icons-material/DarkModeRounded';
import SettingsOutlinedIcon from '@mui/icons-material/SettingsOutlined';
import { auth } from '../lib/firebase';
import { useAuth } from '../lib/AuthContext';
import { useColorMode } from '../lib/ThemeContext';
import { TenderCardViewSettings } from './TenderCardViewSettings';

const BRAND_COLORS = {
  blue: '#2D3AF1',
  lilac: '#CF9EFF',
  orange: '#FF7C00',
  charcoal: '#242D32',
  white: '#FFFFFF',
} as const;

export const MOBILE_SEARCH_TOP_NAV_HEIGHT_PX = 114;
export const DESKTOP_TOP_NAV_HEIGHT_PX = 66;
export const MOBILE_TENDER_CARD_CLEARANCE_PX = 60;
export const STICKY_TOP_NAV_HEIGHT_CSS_VAR = '--tenderai-sticky-header-height';

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
    <Box component="span" sx={{ display: 'flex', alignItems: 'center' }}>
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

interface TopNavProps {
  searchControls?: ReactNode;
  filterPanel?: ReactNode;
}

export default function TopNav({ searchControls, filterPanel }: TopNavProps) {
  const { user, isAdmin } = useAuth();
  const location = useLocation();
  const appBarRef = useRef<HTMLElement>(null);
  const [navigationMenuAnchor, setNavigationMenuAnchor] = useState<null | HTMLElement>(null);
  const [settingsMenuAnchor, setSettingsMenuAnchor] = useState<null | HTMLElement>(null);
  const [viewSettingsOpen, setViewSettingsOpen] = useState(false);

  useLayoutEffect(() => {
    setNavigationMenuAnchor(null);
    setSettingsMenuAnchor(null);
    setViewSettingsOpen(false);
  }, [location.pathname, user?.uid]);

  useLayoutEffect(() => {
    const appBar = appBarRef.current;
    if (!appBar) return;

    const root = document.documentElement;
    const previousHeight = root.style.getPropertyValue(STICKY_TOP_NAV_HEIGHT_CSS_VAR);
    const updateHeaderHeight = () => {
      const height = Math.ceil(appBar.getBoundingClientRect().height);
      root.style.setProperty(STICKY_TOP_NAV_HEIGHT_CSS_VAR, `${height}px`);
    };

    updateHeaderHeight();
    const resizeObserver = new ResizeObserver(updateHeaderHeight);
    resizeObserver.observe(appBar);

    return () => {
      resizeObserver.disconnect();
      if (previousHeight) {
        root.style.setProperty(STICKY_TOP_NAV_HEIGHT_CSS_VAR, previousHeight);
      } else {
        root.style.removeProperty(STICKY_TOP_NAV_HEIGHT_CSS_VAR);
      }
    };
  }, []);

  const handleNavigationMenuOpen = (event: MouseEvent<HTMLElement>) => {
    setSettingsMenuAnchor(null);
    setNavigationMenuAnchor(event.currentTarget);
  };

  const handleNavigationMenuClose = () => {
    setNavigationMenuAnchor(null);
  };

  const handleSettingsMenuOpen = (event: MouseEvent<HTMLElement>) => {
    setNavigationMenuAnchor(null);
    setSettingsMenuAnchor(event.currentTarget);
  };

  const handleSettingsMenuClose = () => {
    setSettingsMenuAnchor(null);
  };

  const handleViewSettingsOpen = () => {
    handleSettingsMenuClose();
    setViewSettingsOpen(true);
  };

  const handleViewSettingsClose = () => {
    setViewSettingsOpen(false);
  };

  const handleLogout = () => {
    handleSettingsMenuClose();
    void signOut(auth);
  };

  const hasSearchControls = Boolean(searchControls);

  return (
    <>
      <AppBar ref={appBarRef} position="sticky" color="default" sx={{ overflowAnchor: 'none' }}>
        <Toolbar
          disableGutters
          sx={{
            display: 'grid',
            gridTemplateAreas: {
              xs: hasSearchControls
                ? '"brand actions" "search search"'
                : '"brand actions"',
              md: hasSearchControls
                ? '"brand search actions"'
                : '"brand actions"',
            },
            gridTemplateColumns: {
              xs: 'minmax(0, 1fr) auto',
              md: hasSearchControls
                ? 'auto minmax(0, 1fr) auto'
                : 'minmax(0, 1fr) auto',
            },
            gridTemplateRows: {
              xs: hasSearchControls ? '56px 56px' : '56px',
              md: '64px',
            },
            columnGap: { xs: 1, md: 2 },
            px: { xs: 2, sm: 3 },
            minHeight: { xs: hasSearchControls ? 112 : 56, md: 64 },
          }}
        >
          <Typography
            variant="h6"
            component={RouterLink}
            to="/"
            sx={{
              gridArea: 'brand',
              textAlign: 'left',
              fontWeight: 800,
              textDecoration: 'none',
              whiteSpace: 'nowrap',
              color: (theme) => theme.palette.mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.white,
            }}
          >
            TenderAI
          </Typography>

          {hasSearchControls && (
            <Box
              sx={{
                gridArea: 'search',
                minWidth: 0,
                width: '100%',
                alignSelf: 'center',
              }}
            >
              {searchControls}
            </Box>
          )}

          <Box sx={{ gridArea: 'actions', display: 'flex', gap: 1, alignItems: 'center' }}>
            <Box sx={{ display: { xs: 'none', md: 'flex' }, gap: 1, alignItems: 'center' }}>
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
            </Box>

            <IconButton
              color="inherit"
              aria-label="Open navigation menu"
              aria-controls={navigationMenuAnchor ? 'mobile-navigation-menu' : undefined}
              aria-haspopup="true"
              aria-expanded={navigationMenuAnchor ? 'true' : undefined}
              onClick={handleNavigationMenuOpen}
              sx={{ display: { xs: 'inline-flex', md: 'none' }, width: 48, height: 48 }}
            >
              <MenuIcon />
            </IconButton>

            <Tooltip title="Settings">
              <IconButton
                color="inherit"
                aria-label="Open settings menu"
                aria-controls={settingsMenuAnchor ? 'settings-menu' : undefined}
                aria-haspopup="true"
                aria-expanded={settingsMenuAnchor ? 'true' : undefined}
                onClick={handleSettingsMenuOpen}
                sx={{ width: { xs: 48, md: 44 }, height: { xs: 48, md: 44 } }}
              >
                <SettingsOutlinedIcon />
              </IconButton>
            </Tooltip>
          </Box>
        </Toolbar>
        {filterPanel && (
          <Box sx={{ width: '100%' }}>
            {filterPanel}
          </Box>
        )}
      </AppBar>

      <Menu
        id="mobile-navigation-menu"
        anchorEl={navigationMenuAnchor}
        open={Boolean(navigationMenuAnchor)}
        onClose={handleNavigationMenuClose}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { minWidth: 180 } } }}
      >
        <MenuItem
          component={RouterLink}
          to="/"
          selected={location.pathname === '/'}
          onClick={handleNavigationMenuClose}
        >
          Home
        </MenuItem>
        <MenuItem
          component={RouterLink}
          to="/favorites"
          selected={location.pathname === '/favorites'}
          onClick={handleNavigationMenuClose}
        >
          Favorites
        </MenuItem>
        {isAdmin && (
          <MenuItem
            component={RouterLink}
            to="/admin"
            selected={location.pathname.startsWith('/admin')}
            onClick={handleNavigationMenuClose}
          >
            Admin
          </MenuItem>
        )}
      </Menu>

      <Menu
        id="settings-menu"
        anchorEl={settingsMenuAnchor}
        open={Boolean(settingsMenuAnchor)}
        onClose={handleSettingsMenuClose}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { minWidth: 220 } } }}
      >
        {user && (
          <MenuItem onClick={handleViewSettingsOpen}>View Settings</MenuItem>
        )}
        <MenuItem
          component="label"
          sx={{ display: 'flex', justifyContent: 'space-between', gap: 2 }}
        >
          <Typography variant="body2">Dark Mode</Typography>
          <ModeToggle />
        </MenuItem>
        <MenuItem onClick={handleLogout}>Logout</MenuItem>
      </Menu>
      {user && (
        <TenderCardViewSettings
          open={viewSettingsOpen}
          onClose={handleViewSettingsClose}
        />
      )}
    </>
  );
}
