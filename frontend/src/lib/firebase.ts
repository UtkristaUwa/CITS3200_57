import { initializeApp } from 'firebase/app';
import { getAuth, OAuthProvider } from 'firebase/auth';
import { getFirestore } from 'firebase/firestore';
import { getFunctions } from 'firebase/functions';

// From Firebase console → Project settings → Your apps → tenderai-frontend.
const firebaseConfig = {
  apiKey: 'AIzaSyD9G19XEcTM6wTIGRrLyrJqjX2jlILjYCA',
  authDomain: 'tenderai-dev-f0283.firebaseapp.com',
  projectId: 'tenderai-dev',
  storageBucket: 'tenderai-dev.firebasestorage.app',
  messagingSenderId: '170228060686',
  appId: '1:170228060686:web:dddf681da0d52dceca625f',
  measurementId: 'G-E3T0WJBFQ6',
};

const app = initializeApp(firebaseConfig);

export const auth = getAuth(app);
export const db = getFirestore(app);
export const functions = getFunctions(app, 'australia-southeast1');

// Entra ID SSO. The app registration on the client's side is single-tenant, so
// Microsoft will only issue a token to a member of that tenant — that boundary
// is what authorises the user, and the API trusts it (see api/app/auth.py).
//
// `tenant` pins the authorisation endpoint to that one directory rather than
// the shared /common endpoint, which is what stops a personal Microsoft
// account from even reaching the consent screen. Set VITE_ENTRA_TENANT_ID to
// the Directory (tenant) ID from the app registration overview blade.
const ENTRA_TENANT_ID = import.meta.env.VITE_ENTRA_TENANT_ID;

export function microsoftProvider(): OAuthProvider {
  const provider = new OAuthProvider('microsoft.com');

  provider.setCustomParameters({
    // Without this, a browser already signed in to one work account silently
    // reuses it and there is no way to pick another.
    prompt: 'select_account',
    ...(ENTRA_TENANT_ID ? { tenant: ENTRA_TENANT_ID } : {}),
  });

  // The default set. Anything beyond this (Graph mail/Teams for the sharing
  // feature) needs the matching API permission added in Entra first.
  provider.addScope('openid');
  provider.addScope('email');
  provider.addScope('profile');

  return provider;
}
