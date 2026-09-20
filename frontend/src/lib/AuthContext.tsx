import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { onAuthStateChanged, type User } from 'firebase/auth';
import { doc, getDoc, updateDoc } from 'firebase/firestore';
import { auth, db } from './firebase';
import { getMe } from './api';

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
  /** 'microsoft.com' for Entra SSO, 'password' for an invited account. */
  provider: string | null;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  isAdmin: false,
  provider: null,
});

/** Which sign-in method this session actually used, not which are linked. */
function providerOf(user: User): string | null {
  return user.providerData[0]?.providerId ?? null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [provider, setProvider] = useState<string | null>(null);

  useEffect(() => {
    // Firebase keeps this listener updated automatically and it fires once on load with the persisted session, and then again on every login/logout.
    // This is our single source of truth for "am I logged in" across the whole app.
    const unsubscribe = onAuthStateChanged(auth, async (firebaseUser) => {
      setUser(firebaseUser);
      setProvider(firebaseUser ? providerOf(firebaseUser) : null);

      if (firebaseUser) {
        // Look up this user's Firestore profile doc to check admin status.
        // Keyed by uid so it lines up directly with the Auth account.
        try {
          const userDocRef = doc(db, 'users', firebaseUser.uid);
          const userDocSnap = await getDoc(userDocRef);

          if (userDocSnap.exists()) {
            setIsAdmin(userDocSnap.data().isAdmin === true);

            // First successful login proves the password they set themselves
            // works — flip them from pending to active. Accounts created
            // before this field existed have no status at all, which we
            // treat as already-active, so we only touch it when it's
            // explicitly 'pending'.
            if (userDocSnap.data().status === 'pending') {
              await updateDoc(userDocRef, { status: 'active' });
            }
          } else {
            // No profile doc yet. For an Entra user this is simply their first
            // sign-in: their uid did not exist until a moment ago, so nobody
            // could have created it. GET /auth/me provisions the doc server
            // side (api/app/auth.py) and hands back the authoritative
            // isAdmin, which is never true for a freshly provisioned account.
            const me = await getMe();
            setIsAdmin(me.isAdmin);
          }
        } catch (err) {
          // Failing closed matters more than the reason: a Firestore read
          // error or an API that is down must not leave admin routes open.
          console.error('Failed to resolve user profile:', err);
          setIsAdmin(false);
        }
      } else {
        setIsAdmin(false);
      }

      setLoading(false);
    });
    return unsubscribe;
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, isAdmin, provider }}>{children}</AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
