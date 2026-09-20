// Integration tests for the setUserAdmin callable, run against the Firestore
// emulator. Nothing here touches the real tenderai-dev project.
const path = require('node:path');
const FN_DIR = path.resolve(__dirname, '../../functions');

process.env.GCLOUD_PROJECT = 'demo-tenderai';
process.env.FIRESTORE_EMULATOR_HOST = '127.0.0.1:8090';

const admin = require(path.join(FN_DIR, 'node_modules/firebase-admin'));
const { setUserAdmin } = require(path.join(FN_DIR, 'lib/index.js'));
const db = admin.firestore();

const results = [];
const ok = (n) => results.push(['PASS', n]);
const bad = (n, d) => results.push(['FAIL', `${n} :: ${d}`]);

async function expectOk(name, req, check) {
  try {
    const out = await setUserAdmin.run(req);
    const after = (await db.doc('users/' + req.data.uid).get()).data();
    check ? (check(out, after) ? ok(name) : bad(name, 'assertion failed')) : ok(name);
  } catch (e) { bad(name, 'threw ' + (e.code || '') + ' ' + e.message); }
}

async function expectErr(name, req, code) {
  const before = req.data && req.data.uid
    ? (await db.doc('users/' + req.data.uid).get()).data() : null;
  try {
    await setUserAdmin.run(req);
    bad(name, 'succeeded, expected ' + code);
  } catch (e) {
    const after = req.data && req.data.uid
      ? (await db.doc('users/' + req.data.uid).get()).data() : null;
    const unchanged = JSON.stringify(before) === JSON.stringify(after);
    if (e.code !== code) bad(name, `got ${e.code}, expected ${code}`);
    else if (!unchanged) bad(name, 'rejected but the document changed');
    else ok(name);
  }
}

(async () => {
  await db.doc('users/boss').set({ email: 'boss@sva.com.au', isAdmin: true });
  await db.doc('users/boss2').set({ email: 'b2@sva.com.au', isAdmin: true });
  await db.doc('users/staff').set({ email: 'staff@sva.com.au', isAdmin: false });

  const asBoss = (data) => ({ auth: { uid: 'boss' }, data });

  await expectErr('anonymous caller rejected',
    { data: { uid: 'staff', isAdmin: true } }, 'unauthenticated');
  await expectErr('non-admin cannot promote anyone',
    { auth: { uid: 'staff' }, data: { uid: 'staff', isAdmin: true } },
    'permission-denied');
  await expectErr('unknown caller rejected',
    { auth: { uid: 'ghost' }, data: { uid: 'staff', isAdmin: true } },
    'permission-denied');

  await expectOk('admin promotes a user', asBoss({ uid: 'staff', isAdmin: true }),
    (out, after) => out.isAdmin === true && after.isAdmin === true);
  await expectOk('promotion is audited', asBoss({ uid: 'staff', isAdmin: true }),
    (_o, after) => after.adminChangedBy === 'boss' && !!after.adminChangedAt);
  await expectOk('admin demotes another admin',
    asBoss({ uid: 'staff', isAdmin: false }),
    (_o, after) => after.isAdmin === false);
  await expectOk('admin demotes a fellow admin',
    asBoss({ uid: 'boss2', isAdmin: false }),
    (_o, after) => after.isAdmin === false);

  // The invariant: the acting admin cannot remove themselves, so a demotion
  // can never leave the system with zero admins.
  await expectErr('cannot demote self (last-admin guard)',
    asBoss({ uid: 'boss', isAdmin: false }), 'failed-precondition');
  await expectOk('can re-promote self (no-op, allowed)',
    asBoss({ uid: 'boss', isAdmin: true }), (_o, after) => after.isAdmin === true);

  await expectErr('missing profile rejected',
    asBoss({ uid: 'never-signed-in', isAdmin: true }), 'not-found');
  await expectErr('non-boolean isAdmin rejected',
    asBoss({ uid: 'staff', isAdmin: 'yes' }), 'invalid-argument');
  await expectErr('missing uid rejected',
    asBoss({ isAdmin: true }), 'invalid-argument');

  for (const [s, n] of results) console.log(s.padEnd(5), n);
  const failed = results.filter((r) => r[0] === 'FAIL').length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
