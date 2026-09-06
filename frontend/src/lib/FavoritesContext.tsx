import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { doc, getDoc, setDoc, updateDoc, arrayUnion, arrayRemove } from 'firebase/firestore';
import { db } from './firebase';
import { useAuth } from './AuthContext';

interface FavoritesContextValue {
  favorites: Set<string>;
  toggleFavorite: (tenderId: string) => Promise<void>;
  loadingFavorites: boolean;
}

const FavoritesContext = createContext<FavoritesContextValue>({
  favorites: new Set(),
  toggleFavorite: async () => {},
  loadingFavorites: true,
});

export function FavoritesProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [favorites, setFavorites] = useState<Set<string>>(new Set());
  const [loadingFavorites, setLoadingFavorites] = useState(true);

  useEffect(() => {
    let isMounted = true;
    
    async function fetchFavorites() {
      if (!user) {
        if (isMounted) {
          setFavorites(new Set());
          setLoadingFavorites(false);
        }
        return;
      }
      
      try {
        const userRef = doc(db, 'users', user.uid);
        const snap = await getDoc(userRef);
        
        if (snap.exists()) {
          const data = snap.data();
          if (data.favoriteTenderIds && Array.isArray(data.favoriteTenderIds)) {
            if (isMounted) setFavorites(new Set(data.favoriteTenderIds));
          }
        }
      } catch (e) {
        console.error('Failed to fetch favorites from Firestore:', e);
      } finally {
        if (isMounted) setLoadingFavorites(false);
      }
    }

    fetchFavorites();

    return () => {
      isMounted = false;
    };
  }, [user]);

  const toggleFavorite = async (tenderId: string) => {
    if (!user) return;
    const userRef = doc(db, 'users', user.uid);
    
    // 1. Optimistic Update: instantly reflect the change in UI without network delay
    const isFav = favorites.has(tenderId);
    setFavorites((prev) => {
      const next = new Set(prev);
      if (isFav) next.delete(tenderId);
      else next.add(tenderId);
      return next;
    });

    // 2. Asynchronously sync to Firestore
    try {
      await updateDoc(userRef, {
        favoriteTenderIds: isFav ? arrayRemove(tenderId) : arrayUnion(tenderId)
      });
    } catch (error: any) {
      // If the document is not found (e.g., new user doc not created yet), initialize it using setDoc with merge
      if (error.code === 'not-found') {
         await setDoc(userRef, { favoriteTenderIds: [tenderId] }, { merge: true });
      } else {
         console.error('Failed to sync favorite to Firestore:', error);
         // If other errors occur, roll back the local React state
         setFavorites((prev) => {
           const next = new Set(prev);
           if (isFav) next.add(tenderId);
           else next.delete(tenderId);
           return next;
         });
      }
    }
  };

  return (
    <FavoritesContext.Provider value={{ favorites, toggleFavorite, loadingFavorites }}>
      {children}
    </FavoritesContext.Provider>
  );
}

export function useFavorites() {
  return useContext(FavoritesContext);
}