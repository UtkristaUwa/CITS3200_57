import { Routes, Route } from 'react-router-dom';
import TendersPage from './pages/TendersPage';
import LoginPage from './pages/LoginPage';
import ForgotPasswordPage from './pages/ForgotPasswordPage';
import { RequireAuth, RedirectIfAuthed } from './lib/RequireAuth';

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
    </Routes>
  );
}