import { useState, useEffect, type FormEvent } from 'react';
import {
  Box,
  Typography,
  TextField,
  Button,
  Checkbox,
  FormControlLabel,
  Alert,
  CircularProgress,
  Paper,
  Table,
  TableHead,
  TableRow,
  TableCell,
  TableBody,
  TableContainer,
  Chip,
} from '@mui/material';
import { httpsCallable } from 'firebase/functions';
import { functions, db } from '../lib/firebase';
import { collection, getDocs } from 'firebase/firestore';

interface UserRow {
  id: string;
  email: string;
  isAdmin: boolean;
  createdAt: string;
  status: 'active' | 'pending';
}

export default function UserManagementPage() {
  const [email, setEmail] = useState('');
  const [makeAdmin, setMakeAdmin] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteSuccess, setInviteSuccess] = useState<string | null>(null);

  const [users, setUsers] = useState<UserRow[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const loadUsers = async () => {
    setLoadingUsers(true);
    setListError(null);
    try {
      const snapshot = await getDocs(collection(db, 'users'));
      const rows: UserRow[] = snapshot.docs.map((docSnap) => {
        const data = docSnap.data();
        return {
          id: docSnap.id,
          email: data.email ?? 'Unknown',
          isAdmin: data.isAdmin === true,
          createdAt: data.createdAt?.toDate
            ? data.createdAt.toDate().toLocaleDateString('en-AU')
            : 'Unknown',
          // Accounts created before this field existed have no status at
          // all — treat those as already-active rather than pending.
          status: data.status === 'pending' ? 'pending' : 'active',
        };
      });
      setUsers(rows);
    } catch (err: unknown) {
      setListError(err instanceof Error ? err.message : 'Failed to load users.');
    } finally {
      setLoadingUsers(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

  const handleInvite = async (e: FormEvent) => {
    e.preventDefault();
    setInviteError(null);
    setInviteSuccess(null);
    setInviting(true);

    try {
      const inviteUser = httpsCallable(functions, 'inviteUser');
      const result = await inviteUser({ email, isAdmin: makeAdmin });
      const setupLink = (result.data as { setupLink?: string }).setupLink;
      setInviteSuccess(
        setupLink
          ? `Invited ${email} successfully. Setup link: ${setupLink}`
          : `Invited ${email} successfully.`
      );
      setEmail('');
      setMakeAdmin(false);
      loadUsers();
    } catch (err: unknown) {
      setInviteError(err instanceof Error ? err.message : 'Failed to invite user.');
    } finally {
      setInviting(false);
    }
  };

  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 3 }}>
        User Management
      </Typography>

      <Paper sx={{ p: { xs: 2, sm: 3 }, mb: 3, minWidth: 0 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2 }}>
          Invite a new user
        </Typography>

        {inviteError && <Alert severity="error" sx={{ mb: 2, overflowWrap: 'anywhere' }}>{inviteError}</Alert>}
        {inviteSuccess && <Alert severity="success" sx={{ mb: 2, overflowWrap: 'anywhere' }}>{inviteSuccess}</Alert>}

        <Box
          component="form"
          onSubmit={handleInvite}
          sx={{
            display: 'flex',
            flexDirection: { xs: 'column', sm: 'row' },
            gap: 2,
            alignItems: { xs: 'stretch', sm: 'center' },
            flexWrap: 'wrap',
          }}
        >
          <TextField
            label="Email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            size="small"
            sx={{
              width: { xs: '100%', sm: 'auto' },
              minWidth: { xs: 0, sm: 260 },
              '& .MuiInputBase-root': { minHeight: { xs: 44, sm: 40 } },
            }}
          />
          <FormControlLabel
            control={<Checkbox checked={makeAdmin} onChange={(e) => setMakeAdmin(e.target.checked)} />}
            label="Make admin"
            sx={{ ml: { xs: 0, sm: '-11px' }, mr: { xs: 0, sm: 2 } }}
          />
          <Button
            type="submit"
            variant="contained"
            disabled={inviting}
            sx={{ width: { xs: '100%', sm: 'auto' }, minHeight: { xs: 44, sm: 36 } }}
          >
            {inviting ? <CircularProgress size={20} color="inherit" /> : 'Invite'}
          </Button>
        </Box>
      </Paper>

      <Paper sx={{ p: { xs: 2, sm: 3 }, minWidth: 0 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 2 }}>
          Current users
        </Typography>

        {loadingUsers && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress />
          </Box>
        )}

        {!loadingUsers && listError && <Alert severity="error" sx={{ overflowWrap: 'anywhere' }}>{listError}</Alert>}

        {!loadingUsers && !listError && (
          <TableContainer
            tabIndex={0}
            sx={{ maxWidth: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}
          >
            <Table size="small" aria-label="Current users" sx={{ minWidth: 560 }}>
              <TableHead>
                <TableRow>
                  <TableCell>Email</TableCell>
                  <TableCell>Role</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Invited</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {users.map((u) => (
                  <TableRow key={u.id}>
                    <TableCell sx={{ overflowWrap: 'anywhere' }}>{u.email}</TableCell>
                    <TableCell>
                      {u.isAdmin ? <Chip label="Admin" color="primary" size="small" /> : 'User'}
                    </TableCell>
                    <TableCell>
                      {u.status === 'active' ? (
                        <Chip label="Active" color="success" size="small" />
                      ) : (
                        <Chip label="Pending" color="warning" size="small" />
                      )}
                    </TableCell>
                    <TableCell>{u.createdAt}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>
    </Box>
  );
}
