import { initializeTestEnvironment, assertSucceeds, assertFails } from '@firebase/rules-unit-testing';
import {
  arrayUnion,
  collection,
  deleteDoc,
  deleteField,
  doc,
  getDoc,
  getDocs,
  serverTimestamp,
  setDoc,
  updateDoc,
} from 'firebase/firestore';
import fs from 'node:fs';

const env = await initializeTestEnvironment({
  projectId: 'demo-tenderai',
  firestore: { rules: fs.readFileSync(new URL('../../firestore.rules', import.meta.url), 'utf8'), host: '127.0.0.1', port: 8089 },
});

// Seed profiles the way the Admin SDK would.
await env.withSecurityRulesDisabled(async (ctx) => {
  const db = ctx.firestore();
  await setDoc(doc(db, 'users/alice'), { email: 'a@sva.com.au', isAdmin: false, status: 'pending', active: true });
  await setDoc(doc(db, 'users/bob'), { email: 'b@sva.com.au', isAdmin: true, status: 'active', active: true });
  await setDoc(doc(db, 'users/legacy'), { email: 'legacy@sva.com.au', isAdmin: false, status: 'pending', active: true });
  await setDoc(doc(db, 'pending_users/c@sva.com.au'), { isAdmin: false });
});

const alice = env.authenticatedContext('alice').firestore();
const bob = env.authenticatedContext('bob').firestore();
const legacy = env.authenticatedContext('legacy').firestore();
const anon = env.unauthenticatedContext().firestore();
const results = [];
const check = async (name, p) => {
  try { await p; results.push(['PASS', name]); }
  catch (e) { results.push(['FAIL', name + ' :: ' + e.message.slice(0, 90)]); }
};

const allCompactFields = [
  'atAGlance',
  'atmId',
  'closingDate',
  'agency',
  'source',
  'monetaryValue',
  'openingDate',
  'location',
  'status',
];

const legacyPreferences = ({
  compactFields = ['atAGlance', 'atmId', 'closingDate', 'agency', 'source'],
  jurisdiction = 'Commonwealth',
  status = 'open',
  year = '2026',
  minDate = '2026-01-01',
  maxDate = '2026-12-31',
} = {}) => ({
  schemaVersion: 1,
  cardView: { compactFields },
  defaultFilters: { jurisdiction, status, year, minDate, maxDate },
});

const preferences = ({
  compactFields = ['atAGlance', 'atmId', 'closingDate', 'agency', 'source'],
  jurisdiction = '',
  states = ['NSW'],
  sourceId = 'austender',
  focusAreas = ['health_wellbeing'],
  status = 'open',
  year = '2026',
  minDate = '2026-01-01',
  maxDate = '2026-12-31',
  publishedAfter = '2025-01-01',
  publishedBefore = '2025-12-31',
} = {}) => ({
  schemaVersion: 1,
  cardView: { compactFields },
  defaultFilters: {
    jurisdiction,
    states,
    sourceId,
    focusAreas,
    status,
    year,
    minDate,
    maxDate,
    publishedAfter,
    publishedBefore,
  },
});

const aliceRef = doc(alice, 'users/alice');
const bobRefFromAlice = doc(alice, 'users/bob');
const legacyRef = doc(legacy, 'users/legacy');

// --- the gate ---
await check('signed-out cannot read a profile', assertFails(getDoc(doc(anon, 'users/alice'))));
await check('signed-in can read own profile', assertSucceeds(getDoc(aliceRef)));
await check("ordinary user cannot read another user's profile", assertFails(getDoc(bobRefFromAlice)));
await check('ordinary user cannot list users', assertFails(getDocs(collection(alice, 'users'))));
await check('admin can read another profile', assertSucceeds(getDoc(doc(bob, 'users/alice'))));
await check('signed-in admin can list users (admin page)', assertSucceeds(getDocs(collection(bob, 'users'))));

// Existing writes must keep working for legacy profiles with no preferences.
await check('legacy profile can update favourites without preferences', assertSucceeds(updateDoc(legacyRef, {
  favoriteTenderIds: arrayUnion('legacy-tender'),
})));
await check('legacy profile can complete first login without preferences', assertSucceeds(updateDoc(legacyRef, {
  status: 'active',
})));
await check('legacy profile can update last tender visit without preferences', assertSucceeds(updateDoc(legacyRef, {
  lastTenderVisitAt: serverTimestamp(),
})));

// --- tender preferences: exact schema, values, and self-only access ---
await check('can add valid preferences to own profile', assertSucceeds(updateDoc(aliceRef, {
  tenderPreferences: preferences(),
})));
await check('maximum valid state and focus area arrays are accepted', assertSucceeds(updateDoc(aliceRef, {
  tenderPreferences: preferences({
    states: ['ACT', 'NSW', 'NT', 'QLD', 'SA', 'TAS', 'VIC', 'WA'],
    focusAreas: Array.from({ length: 12 }, (_, i) => `focus_${i}`),
    sourceId: 'dynamic-portal_1',
  }),
})));
await check('legacy v1 default filter shape remains valid', assertSucceeds(updateDoc(aliceRef, {
  tenderPreferences: legacyPreferences({ jurisdiction: 'WA', compactFields: ['location'] }),
})));
await check('can update existing preferences', assertSucceeds(updateDoc(aliceRef, {
  tenderPreferences: preferences({
    compactFields: ['location', 'status'],
    jurisdiction: 'WA',
    status: 'closed',
    year: '2025',
    minDate: '',
    maxDate: '',
  }),
})));
await check('empty compactFields is accepted', assertSucceeds(updateDoc(aliceRef, {
  tenderPreferences: preferences({
    compactFields: [],
    jurisdiction: '',
    status: 'awarded',
    year: '',
    minDate: '',
    maxDate: '',
  }),
})));
await check('all nine compact field IDs are accepted', assertSucceeds(updateDoc(aliceRef, {
  tenderPreferences: preferences({
    compactFields: allCompactFields,
    jurisdiction: 'All jurisdictions',
    status: 'unknown',
    year: '2024',
  }),
})));
await check('jurisdiction length 200 is accepted', assertSucceeds(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: ['atmId'], jurisdiction: 'x'.repeat(200) }),
})));
await check("cannot modify another user's preferences", assertFails(updateDoc(bobRefFromAlice, {
  tenderPreferences: preferences(),
})));

await check('invalid preference schemaVersion is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: { ...preferences(), schemaVersion: 2 },
})));
await check('non-integer preference schemaVersion is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: { ...preferences(), schemaVersion: '1' },
})));
await check('missing top-level preference key is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: {
    schemaVersion: 1,
    cardView: { compactFields: ['atAGlance'] },
  },
})));
await check('unexpected top-level preference key is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: { ...preferences(), extra: true },
})));
await check('missing cardView key is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: { ...preferences(), cardView: {} },
})));
await check('unexpected cardView key is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: {
    ...preferences(),
    cardView: { compactFields: ['atAGlance'], extra: true },
  },
})));
await check('missing defaultFilters key is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: {
    ...preferences(),
    defaultFilters: {
      jurisdiction: '',
      status: '',
      year: '',
      minDate: '',
    },
  },
})));
await check('unexpected defaultFilters key is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: {
    ...preferences(),
    defaultFilters: { ...preferences().defaultFilters, extra: true },
  },
})));
await check('partially extended defaultFilters shape is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: {
    ...legacyPreferences(),
    defaultFilters: { ...legacyPreferences().defaultFilters, states: ['NSW'] },
  },
})));
await check('non-list compactFields is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: 'atAGlance' }),
})));
await check('unknown compact field ID is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: ['atAGlance', 'focusAreas'] }),
})));
await check('duplicate compact field IDs are rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: ['atAGlance', 'atAGlance'] }),
})));
await check('more than nine compact fields are rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: [...allCompactFields, 'status'] }),
})));
await check('invalid default status is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ status: 'pending' }),
})));
await check('invalid default year is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ year: '26' }),
})));
await check('invalid minDate format is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ minDate: '01/01/2026' }),
})));
await check('invalid maxDate format is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ maxDate: '2026-1-01' }),
})));
await check('non-string jurisdiction is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ jurisdiction: 1 }),
})));
await check('non-string status is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ status: true }),
})));
await check('non-string year is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ year: 2026 }),
})));
await check('non-string minDate is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ minDate: null }),
})));
await check('non-string maxDate is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ maxDate: 20261231 }),
})));
await check('jurisdiction length 201 is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ jurisdiction: 'x'.repeat(201) }),
})));
await check('non-list states is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ states: 'NSW' }),
})));
await check('unknown state code is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ states: ['NSW', 'XX'] }),
})));
await check('duplicate states are rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ states: ['NSW', 'NSW'] }),
})));
await check('more than eight states are rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ states: ['ACT', 'NSW', 'NT', 'QLD', 'SA', 'TAS', 'VIC', 'WA', 'NSW'] }),
})));
await check('invalid source ID type is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ sourceId: 1 }),
})));
await check('invalid source ID characters are rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ sourceId: 'portal with spaces' }),
})));
await check('source ID longer than 100 characters is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ sourceId: 'x'.repeat(101) }),
})));
await check('non-list focus areas is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ focusAreas: 'health_wellbeing' }),
})));
await check('duplicate focus areas are rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ focusAreas: ['health_wellbeing', 'health_wellbeing'] }),
})));
await check('invalid dynamic focus area ID is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ focusAreas: ['Health and wellbeing'] }),
})));
await check('non-string focus area ID is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ focusAreas: ['health_wellbeing', 1] }),
})));
await check('more than twelve focus areas are rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ focusAreas: Array.from({ length: 13 }, (_, i) => `focus_${i}`) }),
})));
await check('invalid publishedAfter format is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ publishedAfter: '01/01/2025' }),
})));
await check('invalid publishedBefore type is rejected', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ publishedBefore: null }),
})));
await check('cannot remove tenderPreferences', assertFails(updateDoc(aliceRef, {
  tenderPreferences: deleteField(),
})));
await check('cannot remove a required nested preference field', assertFails(updateDoc(aliceRef, {
  'tenderPreferences.defaultFilters.status': deleteField(),
})));

// Preference writes must not bypass existing update protections.
await check('cannot smuggle invalid status with preferences', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: ['location'] }),
  status: 'disabled',
})));
await check('cannot smuggle invalid last tender visit with preferences', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: ['status'] }),
  lastTenderVisitAt: 'now',
})));
await check('cannot smuggle isAdmin with preferences', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: ['openingDate'] }),
  isAdmin: true,
})));
await check('cannot smuggle email with preferences', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: ['monetaryValue'] }),
  email: 'x@evil.com',
})));
await check('cannot smuggle active with preferences', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: ['agency'] }),
  active: false,
})));
await check('cannot remove a protected field', assertFails(updateDoc(aliceRef, {
  email: deleteField(),
})));

// --- favourites: the one per-user thing, must work ---
await check('can add a favourite to own doc', assertSucceeds(updateDoc(aliceRef, { favoriteTenderIds: arrayUnion('t1') })));
await check('can flip own status pending -> active', assertSucceeds(updateDoc(aliceRef, { status: 'active' })));
await check('preferences cannot bypass status transition validation', assertFails(updateDoc(aliceRef, {
  tenderPreferences: preferences({ compactFields: ['source'] }),
  status: 'pending',
})));

// --- new tender visit state: self-only, server-timestamp-only ---
await check('can update own last tender visit', assertSucceeds(updateDoc(aliceRef, { lastTenderVisitAt: serverTimestamp() })));
await check("cannot update another user's last tender visit", assertFails(updateDoc(doc(alice, 'users/bob'), { lastTenderVisitAt: serverTimestamp() })));
await check('cannot smuggle isAdmin alongside last tender visit', assertFails(updateDoc(aliceRef, { lastTenderVisitAt: serverTimestamp(), isAdmin: true })));
await check('cannot set last tender visit to a non-timestamp', assertFails(updateDoc(aliceRef, { lastTenderVisitAt: 'now' })));

// --- privilege escalation: the reason these rules exist ---
await check('cannot self-grant isAdmin', assertFails(updateDoc(aliceRef, { isAdmin: true })));
await check('cannot smuggle isAdmin alongside a favourite', assertFails(updateDoc(aliceRef, { favoriteTenderIds: arrayUnion('t2'), isAdmin: true })));
await check("cannot write another user's doc", assertFails(updateDoc(doc(alice, 'users/bob'), { favoriteTenderIds: arrayUnion('t1') })));
await check("cannot demote an admin", assertFails(updateDoc(doc(alice, 'users/bob'), { isAdmin: false })));
await check('cannot change own email', assertFails(updateDoc(aliceRef, { email: 'x@evil.com' })));
// Seed a revoked account, then try to switch the API's active check back on.
// (Writing active:true when it is ALREADY true is a no-op that changes no
// fields, so it is allowed and proves nothing — the value has to actually move.)
await env.withSecurityRulesDisabled(async (ctx) => { await setDoc(doc(ctx.firestore(), 'users/alice'), { email: 'a@sva.com.au', isAdmin: false, status: 'active', active: false }); });
await check('cannot reactivate self via active flag', assertFails(updateDoc(aliceRef, { active: true })));
await check('cannot recreate a deleted profile', assertFails(setDoc(doc(alice, 'users/carol'), { favoriteTenderIds: ['t1'] })));
await check('cannot delete own profile', assertFails(deleteDoc(aliceRef)));

// --- status must only go pending -> active ---
await env.withSecurityRulesDisabled(async (ctx) => { await setDoc(doc(ctx.firestore(), 'users/alice'), { email: 'a@sva.com.au', isAdmin: false, status: 'disabled', active: false }); });
await check('cannot un-disable self', assertFails(updateDoc(aliceRef, { status: 'active' })));

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
