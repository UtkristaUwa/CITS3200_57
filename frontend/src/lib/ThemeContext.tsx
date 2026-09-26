import { createContext, useContext, useMemo, useState, useEffect, type ReactNode } from 'react';
import { ThemeProvider, createTheme, CssBaseline } from '@mui/material';

type ColorMode = 'light' | 'dark';

interface ColorModeContextValue {
  mode: ColorMode;
  toggleColorMode: () => void;
}

const ColorModeContext = createContext<ColorModeContextValue>({
  mode: 'light',
  toggleColorMode: () => {},
});

export const useColorMode = () => useContext(ColorModeContext);

const STORAGE_KEY = 'tenderai-color-mode';

const BRAND_COLORS = {
  blue: '#2D3AF1',
  lilac: '#CF9EFF',
  orange: '#FF7C00',
  charcoal: '#242D32',
  white: '#FFFFFF',
} as const;

function getInitialMode(): ColorMode {
  if (typeof window === 'undefined') return 'light';
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === 'light' || stored === 'dark') return stored;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function AppThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ColorMode>(getInitialMode);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, mode);
  }, [mode]);

  const toggleColorMode = () => setMode((prev) => (prev === 'light' ? 'dark' : 'light'));

  const theme = useMemo(
    () =>
      createTheme({
        typography: {
          fontFamily: '"Public Sans", Arial, sans-serif',
          h1: { fontWeight: 700 },
          h2: { fontWeight: 700 },
          h3: { fontWeight: 700 },
          h4: { fontWeight: 700 },
          h5: { fontWeight: 700 },
          h6: { fontWeight: 600 },
        },
        palette: {
          mode,
          primary: { main: BRAND_COLORS.blue, contrastText: BRAND_COLORS.white },
          secondary: { main: BRAND_COLORS.lilac, contrastText: BRAND_COLORS.charcoal },
          ...(mode === 'light'
            ? {
                background: { default: BRAND_COLORS.white, paper: BRAND_COLORS.white },
                text: { primary: BRAND_COLORS.charcoal, secondary: BRAND_COLORS.charcoal },
                divider: BRAND_COLORS.lilac,
              }
            : {
                background: { default: BRAND_COLORS.charcoal, paper: '#1B2226' },
                text: { primary: BRAND_COLORS.white, secondary: '#D8DDE0' },
                divider: BRAND_COLORS.lilac,
              }),
        },
        shape: {
          borderRadius: 10,
        },
        components: {
          MuiCssBaseline: {
            styleOverrides: {
              body: {
                fontFamily: '"Public Sans", Arial, sans-serif',
              },
            },
          },
          MuiAppBar: {
            styleOverrides: {
              colorDefault: ({ theme: t }) => ({
                backgroundColor: t.palette.background.paper,
                color: t.palette.text.primary,
                backgroundImage: 'none',
                boxShadow: 'none',
                borderBottom: `2px solid ${BRAND_COLORS.lilac}`,
              }),
            },
          },
          MuiButton: {
            styleOverrides: {
              root: {
                borderRadius: 8,
                fontWeight: 600,
                textTransform: 'none',
              },
            },
          },
          MuiLink: {
            styleOverrides: {
              root: {
                color: mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.lilac,
                fontWeight: 600,
              },
            },
          },
          MuiOutlinedInput: {
            styleOverrides: {
              root: {
                borderRadius: 8,
                '& .MuiOutlinedInput-notchedOutline': {
                  borderColor: mode === 'light' ? BRAND_COLORS.lilac : '#7B688C',
                },
                '&:hover .MuiOutlinedInput-notchedOutline': {
                  borderColor: mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.lilac,
                },
                '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                  borderColor: mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.lilac,
                  borderWidth: 2,
                },
              },
              input: {
                '&::placeholder': {
                  color: mode === 'light' ? BRAND_COLORS.charcoal : '#D8DDE0',
                  opacity: 1,
                },
              },
            },
          },
          MuiInputLabel: {
            styleOverrides: {
              root: {
                color: mode === 'light' ? BRAND_COLORS.charcoal : '#D8DDE0',
                '&.Mui-focused': {
                  color: mode === 'light' ? BRAND_COLORS.blue : BRAND_COLORS.lilac,
                },
              },
            },
          },
          MuiPaper: {
            styleOverrides: {
              root: {
                backgroundImage: 'none',
              },
            },
          },
          MuiCard: {
            styleOverrides: {
              root: {
                backgroundImage: 'none',
              },
            },
          },
          MuiDivider: {
            styleOverrides: {
              root: {
                borderColor: BRAND_COLORS.lilac,
              },
            },
          },
          MuiListItemButton: {
            styleOverrides: {
              root: {
                borderRadius: 8,
                marginInline: 8,
                '&.Mui-selected': {
                  backgroundColor: BRAND_COLORS.lilac,
                  color: BRAND_COLORS.charcoal,
                  '&:hover': { backgroundColor: BRAND_COLORS.lilac },
                },
              },
            },
          },
          MuiMenuItem: {
            styleOverrides: {
              root: {
                '&.Mui-selected': {
                  backgroundColor: BRAND_COLORS.lilac,
                  color: BRAND_COLORS.charcoal,
                  '&:hover': { backgroundColor: BRAND_COLORS.lilac },
                },
              },
            },
          },
          MuiChip: {
            styleOverrides: {
              root: { fontWeight: 600 },
            },
          },
          MuiTableCell: {
            styleOverrides: {
              head: {
                backgroundColor: mode === 'light' ? BRAND_COLORS.lilac : BRAND_COLORS.blue,
                color: mode === 'light' ? BRAND_COLORS.charcoal : BRAND_COLORS.white,
                fontWeight: 700,
              },
            },
          },
        },
      }),
    [mode]
  );

  return (
    <ColorModeContext.Provider value={{ mode, toggleColorMode }}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </ColorModeContext.Provider>
  );
}
