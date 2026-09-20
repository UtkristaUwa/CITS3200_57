import { initializeTestEnvironment, assertSucceeds, assertFails } from '@firebase/rules-unit-testing';
import { doc, getDoc, setDoc, updateDoc, deleteDoc, arrayUnion, collection, getDocs } from 'firebase/firestore';
import fs from 'node:fs';

const env = await initializeTestEnvironment({
  projectId: 'demo-tenderai',
  firestore: { rules: fs.readFileSync(new URL('../../firestore.rules', import.meta.url), 'utf8'), host: '127.0.0.1', port: 8089 },
});

// Seed two profiles the way the Admin SDK would.
await env.withSecurityRulesDisabled(async (ctx) => {
  const db = ctx.firestore();
  await setDoc(doc(db, 'users/alice'), { email: 'a@sva.com.au', isAdmin: false, status: 'pending', active: true });
  await setDoc(doc(db, 'users/bob'), { email: 'b@sva.com.au', isAdmin: true, status: 'active', active: true });
  await setDoc(doc(db, 'pending_users/c@sva.com.au'), { isAdmin: false });
});

const alice = env.authenticatedContext('alice').firestore();
const anon = env.unauthenticatedContext().firestore();
const results = [];
const check = async (name, p) => {
  try { await p; results.push(['PASS', name]); }
  catch (e) { results.push(['FAIL', name + ' :: ' + e.message.slice(0, 90)]); }
};

// --- the gate ---
await check('signed-out cannot read a profile', assertFails(getDoc(doc(anon, 'users/alice'))));
await check('signed-in can read own profile', assertSucceeds(getDoc(doc(alice, 'users/alice'))));
await check('signed-in can list users (admin page)', assertSucceeds(getDocs(collection(alice, 'users'))));

// --- favourites: the one per-user thing, must work ---
await check('can add a favourite to own doc', assertSucceeds(updateDoc(doc(alice, 'users/alice'), { favoriteTenderIds: arrayUnion('t1') })));
await check('can flip own status pending -> active', assertSucceeds(updateDoc(doc(alice, 'users/alice'), { status: 'active' })));

// --- privilege escalation: the reason these rules exist ---
await check('cannot self-grant isAdmin', assertFails(updateDoc(doc(alice, 'users/alice'), { isAdmin: true })));
await check('cannot smuggle isAdmin alongside a favourite', assertFails(updateDoc(doc(alice, 'users/alice'), { favoriteTenderIds: arrayUnion('t2'), isAdmin: true })));
await check("cannot write another user's doc", assertFails(updateDoc(doc(alice, 'users/bob'), { favoriteTenderIds: arrayUnion('t1') })));
await check("cannot demote an admin", assertFails(updateDoc(doc(alice, 'users/bob'), { isAdmin: false })));
await check('cannot change own email', assertFails(updateDoc(doc(alice, 'users/alice'), { email: 'x@evil.com' })));
// Seed a revoked account, then try to switch the API's active check back on.
// (Writing active:true when it is ALREADY true is a no-op that changes no
// fields, so it is allowed and proves nothing — the value has to actually move.)
await env.withSecurityRulesDisabled(async (ctx) => { await setDoc(doc(ctx.firestore(), 'users/alice'), { email: 'a@sva.com.au', isAdmin: false, status: 'active', active: false }); });
await check('cannot reactivate self via active flag', assertFails(updateDoc(doc(alice, 'users/alice'), { active: true })));
await check('cannot recreate a deleted profile', assertFails(setDoc(doc(alice, 'users/carol'), { favoriteTenderIds: ['t1'] })));
await check('cannot delete own profile', assertFails(deleteDoc(doc(alice, 'users/alice'))));

// --- status must only go pending -> active ---
await env.withSecurityRulesDisabled(async (ctx) => { await setDoc(doc(ctx.firestore(), 'users/alice'), { email: 'a@sva.com.au', isAdmin: false, status: 'disabled', active: false }); });
await check('cannot un-disable self', assertFails(updateDoc(doc(alice, 'users/alice'), { status: 'active' })));

// --- pending_users is server-side only ---
await check('cannot read pending_users', assertFails(getDoc(doc(alice, 'pending_users/c@sva.com.au'))));
await check('cannot self-invite', assertFails(setDoc(doc(alice, 'pending_users/me@evil.com'), { isAdmin: true })));

// --- default deny ---
await check('cannot write an unlisted collection', assertFails(setDoc(doc(alice, 'tenders/t1'), { title: 'x' })));

await env.cleanup();
for (const [s, n] of results) console.log(s.padEnd(5), n);
const failed = results.filter((r) => r[0] === 'FAIL').length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
