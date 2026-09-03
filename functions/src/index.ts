import { setGlobalOptions } from "firebase-functions";
import { onCall, HttpsError } from "firebase-functions/v2/https";
import * as admin from "firebase-admin";

admin.initializeApp();

setGlobalOptions({ maxInstances: 10 });

interface InviteUserData {
  email: string;
  isAdmin?: boolean;
}

export const inviteUser = onCall<InviteUserData>(async (request) => {
  // 1. Must be logged in.
  if (!request.auth) {
    throw new HttpsError("unauthenticated", "You must be logged in to invite a user.");
  }

  // 2. Must be an admin — check their own Firestore doc.
  const callerDoc = await admin.firestore().collection("users").doc(request.auth.uid).get();
  if (!callerDoc.exists || callerDoc.data()?.isAdmin !== true) {
    throw new HttpsError("permission-denied", "Only admins can invite users.");
  }

  const { email, isAdmin = false } = request.data;

  if (!email || typeof email !== "string") {
    throw new HttpsError("invalid-argument", "A valid email address is required.");
  }

  // 3. Create the Auth account (no password yet — they'll set one via email).
  let newUser;
  try {
    newUser = await admin.auth().createUser({ email });
  } catch (err: unknown) {
    if (err instanceof Error && "code" in err && (err as { code: string }).code === "auth/email-already-exists") {
      throw new HttpsError("already-exists", "A user with that email already exists.");
    }
    throw new HttpsError("internal", "Failed to create the user account.");
  }

  // 4. Create their Firestore profile doc.
  await admin.firestore().collection("users").doc(newUser.uid).set({
    email,
    isAdmin,
    createdAt: admin.firestore.FieldValue.serverTimestamp(),
    invitedBy: request.auth.uid,
  });

  // 5. Send them a password-setup link (reuses the same mechanism as your reset-password flow).
  const setupLink = await admin.auth().generatePasswordResetLink(email);

  return { uid: newUser.uid, setupLink };
});
