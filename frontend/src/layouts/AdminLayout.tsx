import { Box, List, ListItemButton, ListItemText } from '@mui/material';
import { Outlet, Link as RouterLink, useLocation } from 'react-router-dom';
import TopNav from '../components/TopNav';

const NAV_ITEMS = [
  { label: 'User Management', path: '/admin/users' },
  { label: 'System / Ingestion Health', path: '/admin/health' },
  { label: 'Reference / Config', path: '/admin/config' },
];

export default function AdminLayout() {
  const location = useLocation();

  return (
    <Box sx={{ minHeight: '100vh', bgcolor: '#fcfcfc' }}>
      <TopNav />
      <Box sx={{ display: 'flex' }}>
        <Box sx={{ width: 240, borderRight: '1px solid #e0e0e0', minHeight: 'calc(100vh - 64px)' }}>
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
        <Box sx={{ flexGrow: 1, p: 3 }}>
          <Outlet />
        </Box>
      </Box>
    </Box>
  );
}