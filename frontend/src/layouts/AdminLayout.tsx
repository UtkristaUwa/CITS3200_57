import { Box, List, ListItemButton, ListItemText, MenuItem, TextField } from '@mui/material';
import { Outlet, Link as RouterLink, useLocation, useNavigate } from 'react-router-dom';
import TopNav from '../components/TopNav';

const NAV_ITEMS = [
  { label: 'User Management', path: '/admin/users' },
  { label: 'System / Ingestion Health', path: '/admin/health' },
  { label: 'Reference / Config', path: '/admin/config' },
];

export default function AdminLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const selectedPath = location.pathname === '/admin' ? '/admin/users' : location.pathname;

  return (
    <Box sx={{ minHeight: '100vh', bgcolor: 'background.default' }}>
      <TopNav />
      <Box sx={{ display: 'flex' }}>
        <Box
          sx={{
            display: { xs: 'none', md: 'block' },
            width: 240,
            flexShrink: 0,
            borderRight: '1px solid',
            borderColor: 'divider',
            minHeight: 'calc(100vh - 64px)',
          }}
        >
          <List>
            {NAV_ITEMS.map((item) => (
              <ListItemButton
                key={item.path}
                component={RouterLink}
                to={item.path}
                selected={location.pathname === item.path}
              >
                <ListItemText primary={item.label} />
              </ListItemButton>
            ))}
          </List>
        </Box>
        <Box sx={{ flexGrow: 1, minWidth: 0, p: { xs: 2, sm: 3 } }}>
          <TextField
            select
            fullWidth
            size="small"
            label="Admin section"
            value={selectedPath}
            onChange={(event) => navigate(event.target.value)}
            sx={{
              display: { xs: 'block', md: 'none' },
              mb: 3,
              maxWidth: { sm: 420 },
              '& .MuiInputBase-root': { minHeight: 44 },
            }}
          >
            {NAV_ITEMS.map((item) => (
              <MenuItem key={item.path} value={item.path}>
                {item.label}
              </MenuItem>
            ))}
          </TextField>
          <Outlet />
        </Box>
      </Box>
    </Box>
  );
}
