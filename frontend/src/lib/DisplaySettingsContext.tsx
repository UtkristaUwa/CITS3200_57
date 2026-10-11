import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { getDisplaySettings, type DisplaySettings } from './api';
import { useAuth } from './AuthContext';

// Shared admin view toggles, read once per session. Both are admin-only, so a
// non-admin never calls the endpoint (it would 403) and always sees the
// defaults — which matters because what they can see is enforced by the API,
// not by these.
const DEFAULTS: DisplaySettings = { show_json: false, show_irrelevant: false };

interface DisplaySettingsValue {
  settings: DisplaySettings;
  loading: boolean;
  /** Replaces the cached settings after an admin saves them. */
  setSettings: (settings: DisplaySettings) => void;
  reload: () => Promise<void>;
}

const DisplaySettingsContext = createContext<DisplaySettingsValue>({
  settings: DEFAULTS,
  loading: false,
  setSettings: () => {},
  reload: async () => {},
});

export function DisplaySettingsProvider({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth();
  const [settings, setSettings] = useState<DisplaySettings>(DEFAULTS);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    if (!isAdmin) {
      setSettings(DEFAULTS);
      return;
    }
    setLoading(true);
    try {
      setSettings(await getDisplaySettings());
    } catch {
      // A settings outage must not blank the tender list, so fall back to the
      // defaults: everything hidden that would normally be hidden.
      setSettings(DEFAULTS);
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const value = useMemo(
    () => ({ settings, loading, setSettings, reload }),
    [settings, loading, reload],
  );

  return (
    <DisplaySettingsContext.Provider value={value}>{children}</DisplaySettingsContext.Provider>
  );
}

export function useDisplaySettings(): DisplaySettingsValue {
  return useContext(DisplaySettingsContext);
}
