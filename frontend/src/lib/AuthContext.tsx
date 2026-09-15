import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { onAuthStateChanged, type User } from 'firebase/auth';
import { doc, getDoc, updateDoc } from 'firebase/firestore';
import { auth, db } from './firebase';

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
}

const AuthContext = createContext<AuthContextValue>({ user: null, loading: true, isAdmin: false });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    // Firebase keeps this listener updated automatically and it fires once on load with the persisted session, and then again on every login/logout.
    // This is our single source of truth for "am I logged in" across the whole app.
    const unsubscribe = onAuthStateChanged(auth, async (firebaseUser) => {
      setUser(firebaseUser);

      if (firebaseUser) {
        // Look up this user's Firestore profile doc to check admin status.
        // Keyed by uid so it lines up directly with the Auth account.
        try {
          const userDocRef = doc(db, 'users', firebaseUser.uid);
          const userDocSnap = await getDoc(userDocRef);
          setIsAdmin(userDocSnap.exists() && userDocSnap.data().isAdmin === true);

          // First successful login proves the password they set themselves
          // works — flip them from pending to active. Accounts created
          // before this field existed have no status at all, which we
          // treat as already-active, so we only touch it when it's
          // explicitly 'pending'.
          if (userDocSnap.exists() && userDocSnap.data().status === 'pending') {
            await updateDoc(userDocRef, { status: 'active' });
          }
        } catch (err) {
          console.error('Failed to fetch user profile doc:', err);
          setIsAdmin(false);
        }
      } else {
        setIsAdmin(false);
      }

      setLoading(false);
    });
    return unsubscribe;
  }, []);

  return <AuthContext.Provider value={{ user, loading, isAdmin }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}