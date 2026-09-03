import { Routes, Route } from 'react-router-dom';
import TendersPage from './pages/TendersPage';
import LoginPage from './pages/LoginPage';
import ForgotPasswordPage from './pages/ForgotPasswordPage';
import ResetPasswordPage from './pages/ResetPasswordPage';
import { RequireAuth, RedirectIfAuthed, RequireAdmin } from './lib/RequireAuth';
import AdminLayout from './layouts/AdminLayout';
import UserManagementPage from './pages/UserManagementPage';
import SystemHealthPage from './pages/SystemHealthPage';
import ConfigPage from './pages/ConfigPage';

export default function App() {
  return (
    <Routes>
      <Route
        path="/"
        element={
          <RequireAuth>
            <TendersPage />
          </RequireAuth>
        }
      />
      <Route
        path="/login"
        element={
          <RedirectIfAuthed>
            <LoginPage />
          </RedirectIfAuthed>
        }
      />
      <Route
        path="/forgot-password"
        element={
          <RedirectIfAuthed>
            <ForgotPasswordPage />
          </RedirectIfAuthed>
        }
      />
      <Route
        path="/reset-password"
        element={
          <ResetPasswordPage />
        }
      />

      <Route
        path="/admin"
        element={
          <RequireAdmin>
            <AdminLayout />
          </RequireAdmin>
        }
      >
        <Route index element={<UserManagementPage />} />
        <Route path="users" element={<UserManagementPage />} />
        <Route path="health" element={<SystemHealthPage />} />
        <Route path="config" element={<ConfigPage />} />
      </Route>
    </Routes>
  );
}