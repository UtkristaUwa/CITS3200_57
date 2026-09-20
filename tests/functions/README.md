# Cloud Functions tests

Integration tests for the `setUserAdmin` callable, run against the Firestore
emulator. No cloud project is touched and no credentials are needed.

```bash
cd tests/functions
npm install
npm test          # builds ../../functions first, then runs the suite
```

Needs a JDK for the emulator; `firebase-tools` is pinned to 13.x so Java 11
works (14+ wants Java 21). Also run `npm install` in `functions/` once — the
tests load `firebase-admin` and the compiled `lib/index.js` from there.

## Why this function exists

`firestore.rules` deliberately makes `users/{uid}.isAdmin` unwritable from the
browser, since the same document holds a user's favourites. `setUserAdmin` runs
on the Admin SDK, which bypasses rules, and is therefore the only path that can
change the field.

## The invariant worth knowing

The function refuses to let an admin demote **themselves**. That single rule is
what guarantees the system can never end up with zero admins: whoever performs a
demotion is an admin and remains one, so the admin set can never be emptied. No
admin counting and no transaction is needed to hold that property — which is why
you should not "helpfully" relax the self-demotion check later.

Covered: unauthenticated and non-admin callers, an unknown caller, promotion and
demotion (including of a fellow admin), the audit fields, self-demotion, a
profile that does not exist yet, and bad arguments. Every rejection also asserts
the target document was left untouched.
