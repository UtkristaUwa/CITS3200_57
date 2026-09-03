import { initializeApp } from 'firebase/app';
import { getAuth } from 'firebase/auth';
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