# Entra ID SSO — setup checklist

TenderAI keeps email/password sign-in and adds Microsoft Entra ID alongside it.
Both methods end up at the same place: a **Firebase ID token**, which the API
verifies on every request.

```
 Staff member
     │
     ├─ "Sign in with Microsoft" ──▶ login.microsoftonline.com/<SVA tenant>
     │                                        │
     │                               tenderai-dev-f0283.firebaseapp.com/__/auth/handler
     │                                        │
     └─ email + password ──────────────▶ Firebase Auth
                                              │  Firebase ID token (JWT, ~1 hour)
                                              ▼
                                   FastAPI  api/app/auth.py
                                   verify → authorise → users/{uid}
```

Firebase is the only thing that ever sees the Entra client secret. The React app
never handles it, and MSAL is not needed.

**Access model:** the Entra app registration is **single-tenant**, so Microsoft
only ever issues a token to a member of SVA's directory. That boundary *is* the
access control — a tenant member signing in for the first time is
auto-provisioned as an ordinary, non-admin user, because their Firebase uid does
not exist until that moment. Password accounts are unchanged: still invite-only,
still created up front by the `inviteUser` Cloud Function. **Nobody becomes an
admin automatically** by either route.

---

## Part A — Entra app registration (needs an SVA Entra admin)

Azure portal → **Microsoft Entra ID** → **App registrations** → **New registration**.

| Field | Value |
|---|---|
| Name | `TenderAI (UWA CITS3200)` |
| Supported account types | **Accounts in this organizational directory only — single tenant** |
| Redirect URI | Platform **Web** → `https://tenderai-dev-f0283.firebaseapp.com/__/auth/handler` |

> The redirect URI is the Firebase auth handler, and the subdomain really is
> `tenderai-dev-f0283`, not `tenderai-dev`. Getting this wrong is the single
> most common cause of `AADSTS50011: redirect URI mismatch`.

Then:

1. **Overview** blade — copy the **Application (client) ID** and the
   **Directory (tenant) ID**. Both are needed below.
2. **Certificates & secrets** → **New client secret**. Description `firebase-auth`,
   expiry 12 or 24 months. **Copy the `Value` column immediately** — it is shown
   once and never again. (The `Secret ID` is not the secret.)
3. **API permissions** → Microsoft Graph → Delegated: `openid`, `email`,
   `profile`, `User.Read`. Most of these are default. If the tenant requires
   admin consent, click **Grant admin consent for SVA**.
4. *(Optional but recommended for a pilot)* **Enterprise applications** →
   TenderAI → **Properties** → **Assignment required? = Yes**, then
   **Users and groups** → assign only the staff who should have access. This is
   the knob that narrows "anyone in the tenant" to "these people", without any
   code change.

**Note on secret expiry:** sign-in breaks the day the secret expires. Put the
expiry date in the risk register and in the team calendar.

---

## Part B — Firebase console

Firebase console → project **tenderai-dev** → **Authentication**.

1. **Sign-in method** → **Add new provider** → **Microsoft**.
2. Toggle **Enable**, paste the **Application (client) ID** and the client secret
   **Value** from Part A.
3. Confirm the **callback URL** Firebase displays matches the redirect URI you
   registered. Copy it from here if in doubt — it is authoritative.
4. **Save.** Leave **Email/Password** enabled; these coexist.
5. **Settings → Authorized domains** — `localhost`,
   `tenderai-dev-f0283.firebaseapp.com` and `tenderai-dev-f0283.web.app` must all
   be listed. (There are also ~10 dead PR-preview channels in there; harmless,
   worth tidying.)
6. **Settings → User account linking** — decide before testing:
   - *One account per email* (default): a person who already has an invited
     password account and then clicks "Sign in with Microsoft" gets
     `auth/account-exists-with-different-credential`. The login page explains
     this and tells them to use their password instead.
   - Switching to multiple accounts per email gives one human two uids and two
     profile docs. **Don't.**

The tenant is pinned client-side via `VITE_ENTRA_TENANT_ID` (see Part C), which
sends users to SVA's login page rather than the shared `/common` endpoint.

---

## Part C — Environment variables

**`frontend/.env.local`** (local dev, not committed):

```
VITE_API_BASE_URL=http://localhost:8000
VITE_ENTRA_TENANT_ID=<Directory (tenant) ID from Part A>
```

**`frontend/.env.production`** — add the same `VITE_ENTRA_TENANT_ID` line. It is
a public identifier, not a secret; it ships in the bundle by design.

**`api/.env`** (from `.env.example`) and **`api/env.yaml`** (already updated):

```
AUTO_PROVISION_SSO=true      # false = every user, SSO included, needs a pre-created profile
ALLOWED_EMAIL_DOMAINS=       # optional extra guard, e.g. sva.com.au — empty accepts whatever the tenant issues
```

Redeploy Cloud Run with `--env-vars-file env.yaml`.

> **Deploy the API and the frontend together.** The API now rejects unauthenticated
> requests, and the old frontend bundle sends no token — shipping one without the
> other gives every user a 401 on the tenders page.

---

## Part D — Test plan

| # | Test | Expected |
|---|---|---|
| 1 | `npm run dev` + `uvicorn app.main:app --reload`, click **Sign in with Microsoft** | SVA-branded Microsoft login, not a generic one |
| 2 | Complete sign-in as a tenant member who has never used the app | Lands on the tenders page; Firestore gains `users/{uid}` with `autoProvisioned: true`, `isAdmin: false`, `status: "active"` |
| 3 | Sign in again as the same user | No duplicate doc, no change to `isAdmin` |
| 4 | Existing invited password account logs in | Still works, unchanged |
| 5 | Personal `@outlook.com` account | Rejected by Microsoft before Firebase is reached |
| 6 | `curl $API/tenders` with no header | `401 missing bearer token` |
| 7 | `curl $API/tenders -H "Authorization: Bearer garbage"` | `401 invalid token` |
| 8 | Sign in with Microsoft, then from an existing admin account promote that new uid on `/admin/users` | Admin nav appears for the SSO account after reload; the switch on your own row is disabled |
| 9 | Leave the tab open >1 hour, then load tenders | Silent token refresh, no visible error |

---

## Part E — Known gaps this work does *not* close

1. **`firestore.rules` must actually be deployed** — `firebase deploy --only
   firestore:rules`. Until then the database is on whatever ruleset the console
   has, and a user can write their own `isAdmin` (favourites and `isAdmin` share
   one document). Tests: `cd tests/firestore_rules && npm install && npm test`.
2. **`inviteUser` sends a `http://localhost:5173/reset-password` link**
   (`functions/src/index.ts`). Fine for testing, broken for a real invite.
3. **`get-tenders` Cloud Run service** is deployed with invoker IAM disabled and
   has no token check of its own. Only `tenderai-api` is covered here.
4. **`tenderai-api` keeps `allUsers` on `roles/run.invoker`** — deliberately.
   Cloud Run IAM expects Google-issued ID tokens and Firebase tokens are not
   those; removing it breaks the SPA. The FastAPI check is the real gate.
5. **Client secret expiry** — see Part A.

---

## What to send Ramon

> Hi Ramon — to add Microsoft sign-in to TenderAI we need an app registration in
> SVA's Entra tenant. It's a five-minute job for whoever administers your
> Microsoft 365 tenant:
>
> - **New app registration**, name `TenderAI (UWA CITS3200)`
> - **Single tenant** (SVA directory only)
> - **Redirect URI (Web):** `https://tenderai-dev-f0283.firebaseapp.com/__/auth/handler`
> - **A client secret**, 12-month expiry
> - Delegated Graph permissions `openid`, `email`, `profile`, `User.Read`, with
>   admin consent granted
>
> We then need the **Application (client) ID**, the **Directory (tenant) ID**, and
> the **client secret value** — the secret should come through a secure channel,
> not email, and it only ever gets stored in Firebase, never in our repo.
>
> If you'd rather not open it to the whole directory, set **Assignment required =
> Yes** on the enterprise app and assign just the people who should have access —
> we don't need to change anything our end for that.
