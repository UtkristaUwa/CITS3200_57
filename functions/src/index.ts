import {setGlobalOptions} from "firebase-functions";
import {onCall, HttpsError} from "firebase-functions/v2/https";
import * as admin from "firebase-admin";

admin.initializeApp();

setGlobalOptions({maxInstances: 10, region: "australia-southeast1"});

interface InviteUserData {
  email: string;
  isAdmin?: boolean;
}

export const inviteUser = onCall<InviteUserData>(async (request) => {
  // 1. Must be logged in.
  if (!request.auth) {
    throw new HttpsError(
      "unauthenticated",
      "You must be logged in to invite a user."
    );
  }

  // 2. Must be an admin — check their own Firestore doc.
  const callerRef = admin.firestore()
    .collection("users")
    .doc(request.auth.uid);
  const callerDoc = await callerRef.get();

  if (!callerDoc.exists || callerDoc.data()?.isAdmin !== true) {
    throw new HttpsError(
      "permission-denied",
      "Only admins can invite users."
    );
  }

  const {email, isAdmin = false} = request.data;

  if (!email || typeof email !== "string") {
    throw new HttpsError(
      "invalid-argument",
      "A valid email address is required."
    );
  }

  // 3. Create the Auth account (no password yet as they'll set one via email).
  let newUser;
  try {
    newUser = await admin.auth().createUser({email});
  } catch (err: unknown) {
    const isEmailExists =
      err instanceof Error &&
      "code" in err &&
      (err as { code: string }).code === "auth/email-already-exists";

    if (isEmailExists) {
      throw new HttpsError(
        "already-exists",
        "A user with that email already exists."
      );
    }
    throw new HttpsError("internal", "Failed to create the user account.");
  }

  // 4. Create their Firestore profile doc.
  await admin.firestore().collection("users").doc(newUser.uid).set({
    email,
    isAdmin,
    status: "pending",
    createdAt: admin.firestore.FieldValue.serverTimestamp(),
    invitedBy: request.auth.uid,
  });

  const actionCodeSettings = {
    url: "http://localhost:5173/reset-password?flow=invite",
    handleCodeInApp: false,
  };
  const setupLink = await admin.auth()
    .generatePasswordResetLink(email, actionCodeSettings);

  return {uid: newUser.uid, setupLink};
});

interface SetUserAdminData {
  uid: string;
  isAdmin: boolean;
}

export const setUserAdmin = onCall<SetUserAdminData>(async (request) => {
  // 1. Must be logged in.
  if (!request.auth) {
    throw new HttpsError(
      "unauthenticated",
      "You must be logged in to change a user's role."
    );
  }

  // 2. Must be an admin — same check inviteUser does.
  const callerUid = request.auth.uid;
  const callerDoc = await admin.firestore()
    .collection("users")
    .doc(callerUid)
    .get();

  if (!callerDoc.exists || callerDoc.data()?.isAdmin !== true) {
    throw new HttpsError(
      "permission-denied",
      "Only admins can change roles."
    );
  }

  const {uid, isAdmin} = request.data;

  if (!uid || typeof uid !== "string") {
    throw new HttpsError("invalid-argument", "A user id is required.");
  }

  if (typeof isAdmin !== "boolean") {
    throw new HttpsError(
      "invalid-argument",
      "isAdmin must be true or false."
    );
  }

  // 3. Refusing self-demotion is what guarantees the system always keeps at
  // least one admin. Whoever makes the change is an admin and stays one, so
  // no demotion can ever empty the set — which means we do not need to count
  // admins or run this in a transaction to protect that invariant.
  if (uid === callerUid && isAdmin === false) {
    throw new HttpsError(
      "failed-precondition",
      "You can't remove your own admin access. Ask another admin."
    );
  }

  // 4. The profile must already exist. An Entra user has no document until
  // their first sign-in creates one (see api/app/auth.py), so there is
  // nothing to promote before then.
  const targetRef = admin.firestore().collection("users").doc(uid);
  const target = await targetRef.get();

  if (!target.exists) {
    throw new HttpsError(
      "not-found",
      "That user has not signed in yet, so there is no profile to change."
    );
  }

  // This runs on the Admin SDK, which bypasses firestore.rules — the rules
  // deliberately make isAdmin unwritable from the browser, so this function
  // is the only path that can change it.
  await targetRef.update({
    isAdmin,
    adminChangedBy: callerUid,
    adminChangedAt: admin.firestore.FieldValue.serverTimestamp(),
  });

  return {uid, isAdmin};
});
