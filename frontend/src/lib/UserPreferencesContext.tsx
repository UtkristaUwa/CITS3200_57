import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { doc, getDoc, runTransaction } from 'firebase/firestore';
import { useAuth } from './AuthContext';
import { db } from './firebase';
import {
  DEFAULT_TENDER_PREFERENCES,
  PREFERENCE_SCHEMA_VERSION,
  sanitizeDefaultTenderFilters,
  sanitizeTenderCardViewPreferences,
  sanitizeTenderPreferences,
  type DefaultTenderFilters,
  type TenderCardViewPreferences,
  type TenderPreferences,
} from './tenderPreferences';

export interface UserPreferencesContextValue {
  preferences: TenderPreferences;
  ready: boolean;
  loading: boolean;
  saving: boolean;
  error: Error | null;
  saveCardViewPreferences(
    preferences: TenderCardViewPreferences,
  ): Promise<void>;
  saveDefaultTenderFilters(filters: DefaultTenderFilters): Promise<void>;
}

interface StoredPreferencesState {
  ownerUid: string | null;
  generation: number;
  preferences: TenderPreferences;
  ready: boolean;
  loading: boolean;
  pendingSaveCount: number;
  error: Error | null;
}

type PreferencesUpdate = (
  currentPreferences: TenderPreferences,
) => TenderPreferences;

const UserPreferencesContext = createContext<
  UserPreferencesContextValue | undefined
>(undefined);

function asError(error: unknown, fallbackMessage: string): Error {
  return error instanceof Error ? error : new Error(fallbackMessage);
}

export function UserPreferencesProvider({ children }: { children: ReactNode }) {
  const { user, loading: authLoading } = useAuth();
  const currentUid = user?.uid ?? null;
  const generationRef = useRef(0);
  const mountedRef = useRef(false);
  const identityRef = useRef({ uid: currentUid, authLoading });
  const [storedState, setStoredState] = useState<StoredPreferencesState>({
    ownerUid: null,
    generation: 0,
    preferences: DEFAULT_TENDER_PREFERENCES,
    ready: false,
    loading: true,
    pendingSaveCount: 0,
    error: null,
  });

  useLayoutEffect(() => {
    identityRef.current = { uid: currentUid, authLoading };
  }, [authLoading, currentUid]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const operationIsCurrent = useCallback((uid: string, generation: number) => {
    const identity = identityRef.current;
    return mountedRef.current
      && generationRef.current === generation
      && identity.uid === uid
      && !identity.authLoading;
  }, []);

  useEffect(() => {
    const generation = ++generationRef.current;
    let cancelled = false;

    if (authLoading) {
      setStoredState({
        ownerUid: currentUid,
        generation,
        preferences: DEFAULT_TENDER_PREFERENCES,
        ready: false,
        loading: true,
        pendingSaveCount: 0,
        error: null,
      });
      return () => {
        cancelled = true;
        if (generationRef.current === generation) generationRef.current += 1;
      };
    }

    if (!currentUid) {
      setStoredState({
        ownerUid: null,
        generation,
        preferences: DEFAULT_TENDER_PREFERENCES,
        ready: true,
        loading: false,
        pendingSaveCount: 0,
        error: null,
      });
      return () => {
        cancelled = true;
        if (generationRef.current === generation) generationRef.current += 1;
      };
    }

    const uid = currentUid;
    setStoredState({
      ownerUid: uid,
      generation,
      preferences: DEFAULT_TENDER_PREFERENCES,
      ready: false,
      loading: true,
      pendingSaveCount: 0,
      error: null,
    });

    const loadPreferences = async () => {
      try {
        const userSnapshot = await getDoc(doc(db, 'users', uid));
        if (cancelled || !operationIsCurrent(uid, generation)) return;

        if (!userSnapshot.exists()) {
          setStoredState({
            ownerUid: uid,
            generation,
            preferences: DEFAULT_TENDER_PREFERENCES,
            ready: true,
            loading: false,
            pendingSaveCount: 0,
            error: new Error(
              'Your user profile could not be found. Preferences cannot be loaded or saved.',
            ),
          });
          return;
        }

        setStoredState({
          ownerUid: uid,
          generation,
          preferences: sanitizeTenderPreferences(
            userSnapshot.data().tenderPreferences,
          ),
          ready: true,
          loading: false,
          pendingSaveCount: 0,
          error: null,
        });
      } catch (error: unknown) {
        if (cancelled || !operationIsCurrent(uid, generation)) return;

        setStoredState({
          ownerUid: uid,
          generation,
          preferences: DEFAULT_TENDER_PREFERENCES,
          ready: true,
          loading: false,
          pendingSaveCount: 0,
          error: asError(error, 'Failed to load user preferences.'),
        });
      }
    };

    void loadPreferences();

    return () => {
      cancelled = true;
      if (generationRef.current === generation) generationRef.current += 1;
    };
  }, [authLoading, currentUid, operationIsCurrent]);

  const stateMatchesCurrentUser =
    storedState.ownerUid === currentUid
    && storedState.generation === generationRef.current;

  const preferences =
    !authLoading && currentUid && stateMatchesCurrentUser
      ? storedState.preferences
      : DEFAULT_TENDER_PREFERENCES;
  const ready = !authLoading && (
    currentUid === null
      ? true
      : stateMatchesCurrentUser && storedState.ready
  );
  const loading = authLoading || (
    currentUid !== null
    && (!stateMatchesCurrentUser || storedState.loading)
  );
  const saving =
    !authLoading
    && currentUid !== null
    && stateMatchesCurrentUser
    && storedState.pendingSaveCount > 0;
  const error =
    !authLoading && stateMatchesCurrentUser ? storedState.error : null;

  const savePreferences = useCallback(async (
    updatePreferences: PreferencesUpdate,
  ): Promise<void> => {
    const uid = currentUid;
    const generation = generationRef.current;

    if (!uid) {
      const unauthenticatedError = new Error(
        'You must be signed in to save tender preferences.',
      );
      if (!authLoading && mountedRef.current) {
        setStoredState({
          ownerUid: null,
          generation,
          preferences: DEFAULT_TENDER_PREFERENCES,
          ready: true,
          loading: false,
          pendingSaveCount: 0,
          error: unauthenticatedError,
        });
      }
      throw unauthenticatedError;
    }

    if (authLoading || !ready || !stateMatchesCurrentUser) {
      throw new Error('User preferences are not ready to be saved.');
    }

    setStoredState((currentState) => {
      if (
        currentState.ownerUid !== uid
        || currentState.generation !== generation
      ) {
        return currentState;
      }
      return {
        ...currentState,
        pendingSaveCount: currentState.pendingSaveCount + 1,
        error: null,
      };
    });

    try {
      await runTransaction(
        db,
        async (transaction): Promise<void> => {
          const userReference = doc(db, 'users', uid);
          const userSnapshot = await transaction.get(userReference);
          if (!userSnapshot.exists()) {
            throw new Error(
              'Your user profile could not be found. Preferences cannot be saved.',
            );
          }

          const currentPreferences = sanitizeTenderPreferences(
            userSnapshot.data().tenderPreferences,
          );
          const nextPreferences = updatePreferences(currentPreferences);
          transaction.update(userReference, {
            tenderPreferences: nextPreferences,
          });
        },
      );

      if (operationIsCurrent(uid, generation)) {
        setStoredState((currentState) => {
          if (
            currentState.ownerUid !== uid
            || currentState.generation !== generation
          ) {
            return currentState;
          }
          return {
            ...currentState,
            preferences: updatePreferences(currentState.preferences),
          };
        });
      }
    } catch (error: unknown) {
      const saveError = asError(error, 'Failed to save user preferences.');
      if (operationIsCurrent(uid, generation)) {
        setStoredState((currentState) => {
          if (
            currentState.ownerUid !== uid
            || currentState.generation !== generation
          ) {
            return currentState;
          }
          return { ...currentState, error: saveError };
        });
      }
      throw saveError;
    } finally {
      if (operationIsCurrent(uid, generation)) {
        setStoredState((currentState) => {
          if (
            currentState.ownerUid !== uid
            || currentState.generation !== generation
          ) {
            return currentState;
          }
          return {
            ...currentState,
            pendingSaveCount: Math.max(
              0,
              currentState.pendingSaveCount - 1,
            ),
          };
        });
      }
    }
  }, [authLoading, currentUid, operationIsCurrent, ready, stateMatchesCurrentUser]);

  const saveCardViewPreferences = useCallback(async (
    cardViewPreferences: TenderCardViewPreferences,
  ): Promise<void> => {
    const sanitizedCardView = sanitizeTenderCardViewPreferences(
      cardViewPreferences,
    );
    await savePreferences((currentPreferences) => ({
      schemaVersion: PREFERENCE_SCHEMA_VERSION,
      cardView: sanitizedCardView,
      defaultFilters: currentPreferences.defaultFilters,
    }));
  }, [savePreferences]);

  const saveDefaultTenderFilters = useCallback(async (
    defaultFilters: DefaultTenderFilters,
  ): Promise<void> => {
    const sanitizedFilters = sanitizeDefaultTenderFilters(defaultFilters);
    await savePreferences((currentPreferences) => ({
      schemaVersion: PREFERENCE_SCHEMA_VERSION,
      cardView: currentPreferences.cardView,
      defaultFilters: sanitizedFilters,
    }));
  }, [savePreferences]);

  const contextValue = useMemo<UserPreferencesContextValue>(() => ({
    preferences,
    ready,
    loading,
    saving,
    error,
    saveCardViewPreferences,
    saveDefaultTenderFilters,
  }), [
    error,
    loading,
    preferences,
    ready,
    saveCardViewPreferences,
    saveDefaultTenderFilters,
    saving,
  ]);

  return (
    <UserPreferencesContext.Provider value={contextValue}>
      {children}
    </UserPreferencesContext.Provider>
  );
}

export function useUserPreferences(): UserPreferencesContextValue {
  const context = useContext(UserPreferencesContext);
  if (!context) {
    throw new Error(
      'useUserPreferences must be used within a UserPreferencesProvider.',
    );
  }
  return context;
}
