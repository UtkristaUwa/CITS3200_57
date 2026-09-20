# Firestore rules tests

Checks `firestore.rules` against the Firestore emulator — no cloud project, no
credentials, nothing touched in `tenderai-dev`.

```bash
cd tests/firestore_rules
npm install
npm test
```

Needs a JDK for the emulator. `firebase-tools` 14+ requires **Java 21**; it is
pinned to 13.x here so Java 11 also works. If you have Java 21, feel free to
unpin.

## What it covers

The rules exist because the browser must write `users/{uid}` for favourites,
and that is the same document holding `isAdmin` — which gates the admin pages
and the `inviteUser` function. The tests assert both halves:

- favourites and the invited-user `status: pending -> active` flip still work;
- a user cannot grant themselves `isAdmin` (alone or smuggled in alongside a
  favourite), write another user's document, change their own `email` or
  `active`, undo a `disabled` status, create or delete a profile, read or write
  `pending_users`, or touch any collection the rules do not name.

Profile documents are only ever created server side — by `api/app/auth.py` on a
first Entra sign-in, or by `inviteUser` — both via the Admin SDK, which bypasses
rules entirely.

## Watch out

A write whose values are all identical to what is already stored changes no
fields, so the rules allow it. A test that asserts a denial has to make the
value actually move, or it proves nothing.
