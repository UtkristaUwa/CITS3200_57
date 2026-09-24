import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { onAuthStateChanged, type User } from 'firebase/auth';
import {
  Timestamp,
  doc,
  getDoc,
  serverTimestamp,
  updateDoc,
  type DocumentData,
  type DocumentReference,
} from 'firebase/firestore';
import { auth, db } from './firebase';
import { getMe } from './api';

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
  /** 'microsoft.com' for Entra SSO, 'password' for an invited account. */
  provider: string | null;
  previousTenderVisit: number | null;
  firstTenderVisit: boolean;
  tenderVisitReady: boolean;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  isAdmin: false,
  provider: null,
  previousTenderVisit: null,
  firstTenderVisit: true,
  tenderVisitReady: false,
});

interface TenderVisitSession {
  previousTenderVisit: number | null;
  firstTenderVisit: boolean;
  durableVisitRecorded: boolean;
}

const TENDER_VISIT_SESSION_PREFIX = 'tenderai-tender-visit:';

function tenderVisitSessionKey(uid: string): string {
  return `${TENDER_VISIT_SESSION_PREFIX}${uid}`;
}

function readTenderVisitSession(uid: string): TenderVisitSession | null {
  try {
    const stored = window.sessionStorage.getItem(tenderVisitSessionKey(uid));
    if (!stored) return null;

    const parsed = JSON.parse(stored) as Partial<TenderVisitSession>;
    const validPreviousVisit =
      parsed.previousTenderVisit === null
      || (typeof parsed.previousTenderVisit === 'number' && Number.isFinite(parsed.previousTenderVisit));

    if (
      !validPreviousVisit
      || typeof parsed.firstTenderVisit !== 'boolean'
      || typeof parsed.durableVisitRecorded !== 'boolean'
      || (!parsed.firstTenderVisit && parsed.previousTenderVisit === null)
    ) {
      return null;
    }

    return parsed as TenderVisitSession;
  } catch {
    return null;
  }
}

function writeTenderVisitSession(uid: string, state: TenderVisitSession): boolean {
  try {
    window.sessionStorage.setItem(tenderVisitSessionKey(uid), JSON.stringify(state));
    return true;
  } catch (error) {
    console.error('Failed to persist the current tender visit session:', error);
    return false;
  }
}

function previousVisitFromProfile(profile: DocumentData | undefined): number | null {
  const value = profile?.lastTenderVisitAt;
  if (!(value instanceof Timestamp)) return null;

  const milliseconds = value.toMillis();
  return Number.isFinite(milliseconds) ? milliseconds : null;
}

async function resolveTenderVisit(
  uid: string,
  userDocRef: DocumentReference<DocumentData>,
  profile: DocumentData | undefined,
  isCurrentAuthCallback: () => boolean,
): Promise<TenderVisitSession | null> {
  if (!isCurrentAuthCallback()) return null;

  const storedSession = readTenderVisitSession(uid);
  if (storedSession) {
    if (!storedSession.durableVisitRecorded) {
      try {
        if (!isCurrentAuthCallback()) return null;
        await updateDoc(userDocRef, { lastTenderVisitAt: serverTimestamp() });
        if (!isCurrentAuthCallback()) return null;
        storedSession.durableVisitRecorded = true;
        writeTenderVisitSession(uid, storedSession);
      } catch (error) {
        if (!isCurrentAuthCallback()) return null;
        console.error('Failed to record the tender visit timestamp:', error);
      }
    }
    return isCurrentAuthCallback() ? storedSession : null;
  }

  const previousTenderVisit = previousVisitFromProfile(profile);
  const session: TenderVisitSession = {
    previousTenderVisit,
    firstTenderVisit: previousTenderVisit === null,
    durableVisitRecorded: false,
  };

  // Freeze the old cutoff before advancing the durable timestamp so a refresh
  // cannot turn tenders that are New in this use period into old tenders.
  if (!isCurrentAuthCallback()) return null;
  if (writeTenderVisitSession(uid, session)) {
    try {
      if (!isCurrentAuthCallback()) return null;
      await updateDoc(userDocRef, { lastTenderVisitAt: serverTimestamp() });
      if (!isCurrentAuthCallback()) return null;
      session.durableVisitRecorded = true;
      writeTenderVisitSession(uid, session);
    } catch (error) {
      if (!isCurrentAuthCallback()) return null;
      console.error('Failed to record the tender visit timestamp:', error);
    }
  }

  return isCurrentAuthCallback() ? session : null;
}

/** Which sign-in method this session actually used, not which are linked. */
function providerOf(user: User): string | null {
  return user.providerData[0]?.providerId ?? null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [provider, setProvider] = useState<string | null>(null);
  const [previousTenderVisit, setPreviousTenderVisit] = useState<number | null>(null);
  const [firstTenderVisit, setFirstTenderVisit] = useState(true);
  const [tenderVisitReady, setTenderVisitReady] = useState(false);
  const activeUserId = useRef<string | null>(null);
  const authGeneration = useRef(0);

  useEffect(() => {
    let listenerActive = true;

    // Firebase keeps this listener updated automatically and it fires once on load with the persisted session, and then again on every login/logout.
    // This is our single source of truth for "am I logged in" across the whole app.
    const unsubscribe = onAuthStateChanged(auth, async (firebaseUser) => {
      const callbackGeneration = ++authGeneration.current;
      const callbackUserId = firebaseUser?.uid ?? null;
      const isCurrentAuthCallback = () =>
        listenerActive
        && authGeneration.current === callbackGeneration
        && (auth.currentUser?.uid ?? null) === callbackUserId;
      if (!isCurrentAuthCallback()) return;

      setLoading(true);
      setTenderVisitReady(false);

      if (activeUserId.current && activeUserId.current !== firebaseUser?.uid) {
        window.sessionStorage.removeItem(tenderVisitSessionKey(activeUserId.current));
      }
      activeUserId.current = firebaseUser?.uid ?? null;

      setUser(firebaseUser);
      setProvider(firebaseUser ? providerOf(firebaseUser) : null);

      if (firebaseUser) {
        // Look up this user's Firestore profile doc to check admin status.
        // Keyed by uid so it lines up directly with the Auth account.
        try {
          const userDocRef = doc(db, 'users', firebaseUser.uid);
          const userDocSnap = await getDoc(userDocRef);
          if (!isCurrentAuthCallback()) return;
          const profile = userDocSnap.exists() ? userDocSnap.data() : undefined;

          if (userDocSnap.exists()) {
            setIsAdmin(profile?.isAdmin === true);

            // First successful login proves the password they set themselves
            // works — flip them from pending to active. Accounts created
            // before this field existed have no status at all, which we
            // treat as already-active, so we only touch it when it's
            // explicitly 'pending'.
            if (profile?.status === 'pending') {
              await updateDoc(userDocRef, { status: 'active' });
              if (!isCurrentAuthCallback()) return;
            }
          } else {
            // No profile doc yet. For an Entra user this is simply their first
            // sign-in: their uid did not exist until a moment ago, so nobody
            // could have created it. GET /auth/me provisions the doc server
            // side (api/app/auth.py) and hands back the authoritative
            // isAdmin, which is never true for a freshly provisioned account.
            const me = await getMe();
            if (!isCurrentAuthCallback()) return;
            setIsAdmin(me.isAdmin);
          }

          const tenderVisit = await resolveTenderVisit(
            firebaseUser.uid,
            userDocRef,
            profile,
            isCurrentAuthCallback,
          );
          if (!isCurrentAuthCallback() || !tenderVisit) return;
          setPreviousTenderVisit(tenderVisit.previousTenderVisit);
          setFirstTenderVisit(tenderVisit.firstTenderVisit);
        } catch (err) {
          if (!isCurrentAuthCallback()) return;
          // Failing closed matters more than the reason: a Firestore read
          // error or an API that is down must not leave admin routes open.
          console.error('Failed to resolve user profile:', err);
          setIsAdmin(false);
          setPreviousTenderVisit(null);
          setFirstTenderVisit(true);
        }
      } else {
        setIsAdmin(false);
        setPreviousTenderVisit(null);
        setFirstTenderVisit(true);
      }

      if (!isCurrentAuthCallback()) return;
      setTenderVisitReady(true);
      setLoading(false);
    });
    return () => {
      listenerActive = false;
      authGeneration.current += 1;
      unsubscribe();
    };
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isAdmin,
        provider,
        previousTenderVisit,
        firstTenderVisit,
        tenderVisitReady,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
