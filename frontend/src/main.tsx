import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from './lib/AuthContext'
import { FavoritesProvider } from './lib/FavoritesContext' // <-- Import the newly created Provider
import { UserPreferencesProvider } from './lib/UserPreferencesContext'
import { AppThemeProvider } from './lib/ThemeContext'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppThemeProvider>
      <BrowserRouter>
        <AuthProvider>
          <UserPreferencesProvider>
            <FavoritesProvider>  {/* <-- Wrap the App with FavoritesProvider */}
              <App />
            </FavoritesProvider>
          </UserPreferencesProvider>
        </AuthProvider>
      </BrowserRouter>
    </AppThemeProvider>
  </StrictMode>,
)
