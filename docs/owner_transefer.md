# TenderAI Owner Manual

*Prepared by the UWA CITS3200 team (Group 57) for Social Ventures Australia · 8 October 2026*

This manual is for the person at SVA who owns TenderAI after the student team hands it over. Today that is Dr Ramon Wenzel. Parts A to E and G to J are for the owner and need only a web browser. Part F, Microsoft sign-in, is shared: F1 is done on SVA's side, and F2 to F4 by whoever maintains the code. How the system is built in detail lives in the code repository.

```
 Every day, 5 am Perth time
     │
     ▼
 Daily pipeline (Cloud Run job tender-batch-job)
     │  signs in to 7 tender portals (logins kept in Secret Manager)
     │  downloads each tender and its documents
     │  Gemini summarises each tender and scores its relevance to SVA
     │
     ├──────────────▶ Cloud Storage  tenderai-dev-documents   original documents, health report
     ├──────────────▶ BigQuery       TenderAI.tenders          the tender database
     └── on error ──▶ Cloud Monitoring alert ──▶ email to the owner

 Staff member ──▶ Website (Firebase Hosting) ──▶ API tenderai-api ──▶ BigQuery, Cloud Storage
                    │
                    └─ sign-in: Firebase Auth (email + password, or Microsoft)
                       users, admins, favourites: Firestore
```

**Ownership model:** everything in the diagram lives in one Google Cloud project, `tenderai-dev`. Whoever holds the **Owner** role on that project controls the whole system. Billing, the code repository, the portal logins and Microsoft sign-in sit outside the project and each need their own handover.

---

## Part A — What you are taking over

| Item | What it is | Where your steps are |
| --- | --- | --- |
| Google Cloud project `tenderai-dev` | The container for the whole system, in Google's Sydney region | Part C, steps 1 to 3 |
| Billing | The account Google charges for the project's running costs. Today it is a team member's account | Part C, steps 4 and 5 |
| Tender database | BigQuery dataset `TenderAI`. Holds every tender the system has collected | Comes with the project |
| Document storage | Cloud Storage bucket `tenderai-dev-documents`. Holds the original tender documents and the daily health report | Comes with the project |
| Daily pipeline | Scheduled job `tender-batch-job`. Collects and processes tenders at 5 am Perth time | Comes with the project |
| Website and API | Website on Firebase Hosting at `tenderai-dev-f0283.firebaseapp.com`, talking to the backend service `tenderai-api` | Part C, step 7 |
| User accounts | Firebase holds the users, who is an admin, and each person's favourites | Part C, step 8 |
| Failure alert emails | Google Cloud emails a named person when the daily run reports an error. Today that is a team member | Part D |
| Microsoft sign-in | An app registration in SVA's own Microsoft Entra directory. Not switched on yet | Part F |
| Tender portal logins | The usernames and passwords the pipeline uses on portals that need an account | Part G |
| Code repository on GitHub | The source code. Merging a change publishes a new version of the website | Part G |

---

## Part B — Before you start

Have these ready. The transfer itself then takes about 30 minutes and is easiest on a call with the student team.

| You need | Details |
| --- | --- |
| A Google account SVA controls | Ownership attaches to a Google account, so avoid a personal Gmail address. Use an SVA Google Workspace or Cloud Identity account if SVA has one. If not, create a Google account with your SVA email address ("Use my current email address" when signing up). Turn on 2-Step Verification |
| A backup owner | A second SVA person with a Google account set up the same way. If the only owner leaves or loses the account, the project is very hard to recover |
| An SVA Cloud Billing account | A payment profile in Google Cloud, with a card or invoicing, on which you are **Billing Account Administrator**. Create one at console.cloud.google.com/billing if SVA does not have one |
| Your exact Google account email, sent to the team | They use it to invite you. A typo or an alias sends the invitation to an account you cannot sign in with |

---

## Part C — Taking ownership of the Google Cloud project

> There is no "transfer" button in Google Cloud. Ownership moves in three stages: the team adds you as an Owner, you move billing and settings to SVA, and then the team is removed. Do not remove the team (step 10) until billing is on SVA's account and the checks in Part E pass.

1. **Send the team your Google account email.** The team grants you the **Owner** role on `tenderai-dev`. This is their only action; everything below is yours.
2. **Accept the invitation.** Google emails you an invitation to join the project as Owner. Open it while signed in to the same Google account and accept. You are not an owner until you accept.
    - No email after a few minutes? Check spam, then ask the team to confirm the address they entered.
    - Still nothing? Sign in at console.cloud.google.com and look in the notifications bell, or ask the team to send you the invitation link directly.
3. **Confirm you are an Owner.** At console.cloud.google.com choose `tenderai-dev` in the project picker, then open **IAM & Admin > IAM**. Your email should show the role **Owner**. New access can take a few minutes to appear.
4. **Move billing to SVA.** Open **Billing**, find `tenderai-dev` in the projects list, open the three-dot **Actions** menu on its row, choose **Change billing**, select the SVA billing account and click **Set account**.
    - You need to be Owner of the project and Billing Account Administrator or User on the SVA billing account.
    - Costs before the switch stay on the old account; costs after it go to SVA.
5. **Set a budget alert.** In **Billing > Budgets & alerts**, create a budget for `tenderai-dev` with a monthly amount you are comfortable with and email alerts at 50%, 90% and 100%. A budget warns you; it does not stop spending.
6. **Add your backup owner.** In **IAM & Admin > IAM**, click **Grant access**, enter the backup's Google account, choose **Owner** and click **Save**. They accept an invitation the same way you did.
7. **Check Firebase.** Sign in at console.firebase.google.com with the same Google account. `tenderai-dev` should appear, and **Project settings > Users and permissions** should show you as Owner. Nothing to change; this confirms the website, user accounts and sign-in came across.
8. **Confirm your admin rights in TenderAI.** Your TenderAI account is already an admin. Sign in to the website and check that **Admin** appears in the top menu.
    - Make your backup an admin too: once they have a TenderAI account, turn on the switch in the Role column on their row under **Admin > User management**.
    - Do this before the team leaves. An admin cannot remove their own admin rights, so you cannot lock yourself out, but only an existing admin can make new ones.
9. **Move the failure alert emails to SVA.** Follow Part D.
10. **Remove the team's access.** Only after Part E passes and the Billing page shows `tenderai-dev` on SVA's billing account.
    - In **IAM & Admin > IAM**, for each student account click **Edit principal**, delete every role and click **Save**.
    - In TenderAI, under **Admin > User management**, turn off the admin switch on every team account.
    - The Admin pages have no remove or disable button. Before they go, the team disables their own accounts: in Firestore, set `status` to `disabled` on their `users/{uid}` record (the API refuses them from the next request), and disable the account under **Authentication > Users** in the Firebase console.
    - Leave any entry ending in `gserviceaccount.com` alone. These are the identities the system itself runs under, not people.
    - If you have agreed a support period, keep one team member on a reduced role such as **Editor** until it ends.

> **Billing does not move by itself.** Handing over ownership or removing the team leaves the project on the team member's billing account, still charging them, until step 4 is done. Once removed, they can no longer even see it.

---

## Part D — Moving the failure alert emails

The failure email is a Cloud Monitoring alert policy called **Pipeline finished, Scraper error**. It emails everyone on its notification channels whenever the daily run ends with an error. Today that is one team member. Moving it takes about ten minutes.

Monitoring → **Alerting** → **Edit notification channels** → **Email** → **Add new**.

| Field | Value |
| --- | --- |
| Email address | Your SVA address, then your backup's as a second channel |
| Description | e.g. `Ramon (SVA)` |

> A shared mailbox or group address works too. Set it to accept mail from `alerting-noreply@google.com`.

Then:

1. **Open Alerting.** Go to console.cloud.google.com/monitoring/alerting and choose `tenderai-dev` in the project picker.
2. **Add the channels** using the table above, and click **Save** for each.
3. **Open the policy.** On **Alerting**, click **See all policies**, find **Pipeline finished, Scraper error**, open the three-dot **More options** menu on its row and choose **Edit**.
4. **Change who it notifies.** In **Notifications and name**, tick the SVA channels and untick the team member's. Click **Save policy**.
5. **Check for other policies** that notify a team member, and change them the same way.
6. **Add `alerting-noreply@google.com` to your Outlook safe senders.** Outlook otherwise blocks parts of these emails, including the **View incident** button.
7. **Tidy up after handover.** The console cannot delete a channel a policy still uses. Once no policy lists the team member's channel, delete it under **Edit notification channels**.

The next run that reports an error confirms it works. Each email lists the error codes 0 to 6; the **System / ingestion health** page shows which portal failed.

---

## Part E — Checks after the transfer

Run these after step 9 of Part C, with the team still on the call. If any fail, the team can still fix them.

| # | Check | Expected |
| --- | --- | --- |
| 1 | **IAM & Admin > IAM** | You and your backup listed as **Owner** |
| 2 | **Billing**, project `tenderai-dev` | Linked to the SVA billing account; the budget alert exists |
| 3 | **Monitoring > Alerting**, policy **Pipeline finished, Scraper error** | Notifies an SVA email address |
| 4 | Open TenderAI and sign in with your own account | Lands on the tenders page |
| 5 | **Admin > User management** | You and your backup shown as admins |
| 6 | **Admin > System / ingestion health** | A Last Run from this morning for each portal |
| 7 | Home page | Tenders, including some published in the last few days |

> After step 10, run checks 4 to 7 again the next day. That confirms nothing was quietly depending on a student's account.

---

## Part F — Microsoft sign-in

TenderAI keeps email and password sign-in and adds Microsoft sign-in alongside it, so SVA staff can use their existing Microsoft 365 work account. Both methods end at the same place: a **Firebase ID token**, which the API checks on every request.

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

Firebase is the only thing that ever sees the Entra client secret. The website never handles it, and it is never stored in the repository.

**Access model:** the app registration is **single-tenant**, so Microsoft only issues a token to a member of SVA's directory. That boundary is the access control. A member signing in for the first time becomes an ordinary, non-admin user. Password accounts are unchanged: still invite-only, created by the `inviteUser` Cloud Function. **Nobody becomes an admin automatically** by either route.

SVA already has a Microsoft Entra directory (it is what SVA's Microsoft 365 accounts sign in through). What is missing is TenderAI's registration in it.

| Part | Who | Time |
| --- | --- | --- |
| F1 Entra app registration | Ramon, or SVA's Microsoft 365 administrator | 15 minutes |
| F2 Firebase console | Developer, or Ramon as project Owner | 5 minutes |
| F3 Settings and publishing | Developer | 10 minutes |
| F4 Test plan | Developer, with one SVA staff member | 15 minutes |

### F1 — Entra app registration (SVA side)

> **Who can do it:** the Entra role **Cloud Application Administrator**, **Application Administrator** or Global Administrator. A normal staff account can register the app but cannot grant admin consent or restrict who signs in. If you don't hold one of these roles, ask SVA's Microsoft 365 administrator.

entra.microsoft.com → **Entra ID** → **App registrations** → **New registration**. (The Azure portal's **Microsoft Entra ID** menu leads to the same place.) If you belong to more than one directory, switch to SVA's with the **Settings** icon first.

| Field | Value |
| --- | --- |
| Name | `TenderAI (UWA CITS3200)` |
| Supported account types | **Single tenant only** (accounts in SVA's directory only) |
| Redirect URI | Platform **Web** → `https://tenderai-dev-f0283.firebaseapp.com/__/auth/handler` |

> The redirect URI is the Firebase sign-in handler, and the subdomain really is `tenderai-dev-f0283`, not `tenderai-dev`. Getting this wrong is the most common cause of `AADSTS50011: redirect URI mismatch`.

Then:

1. **Overview** — copy the **Application (client) ID** and the **Directory (tenant) ID**. Both are needed below. They are identifiers, not secrets.
2. **Certificates & secrets** → **Client secrets** → **New client secret**. Description `firebase-auth`, expiry 12 months (Microsoft's recommendation; 24 is the maximum). **Copy the `Value` column immediately**: it is shown once and never again. The `Secret ID` is not the secret.
3. **API permissions** → Microsoft Graph → Delegated: `openid`, `email`, `profile`, `User.Read`. Most are there by default; add any missing with **Add a permission**. Then click **Grant admin consent for SVA**.
4. *(Optional, recommended)* **Entra ID** → **Enterprise apps** → **All applications** → TenderAI → **Properties** → **Assignment required? = Yes**, then **Users and groups** → **Add user/group** → assign only the staff who should have access. Assigning a whole group needs an Entra ID P1 or P2 licence; on the free tier, add people one at a time. With **No**, anyone in SVA's directory can sign in.

**Handing over:** email the client ID and tenant ID to the developer. Send the secret value through a password manager share, or read it out on a call — never by email or chat.

> **Secret expiry:** Microsoft sign-in breaks for everyone the day the secret expires. Put the date in a calendar with a reminder a month before. Renewal is in F5.

### F2 — Firebase console

Firebase console → project **tenderai-dev** → **Authentication**.

1. **Sign-in method** → **Add new provider** → **Microsoft**.
2. Toggle **Enable**, paste the **Application (client) ID** and the client secret **Value** from F1.
3. Confirm the **callback URL** Firebase shows matches the redirect URI registered in F1. If in doubt, copy it from here: Firebase's is the authoritative one.
4. **Save.** Leave **Email/Password** enabled; the two work side by side.
5. **Settings → Authorized domains** — `localhost`, `tenderai-dev-f0283.firebaseapp.com` and `tenderai-dev-f0283.web.app` must all be listed. Old pull-request preview addresses in the list are harmless but worth tidying.
6. **Settings → User account linking** — keep **one account per email** (the default). A person who already has an invited password account and clicks "Sign in with Microsoft" is told to use their password instead. Switching to multiple accounts per email gives one person two TenderAI accounts. **Don't.**

### F3 — Settings and publishing

| File | Setting | Notes |
| --- | --- | --- |
| `frontend/.env.local` (local development, not committed) | `VITE_API_BASE_URL=http://localhost:8000`, `VITE_ENTRA_TENANT_ID=<tenant ID>` | Developer's own machine only |
| `frontend/.env.production` (committed) | `VITE_ENTRA_TENANT_ID=<tenant ID>` | A public identifier, not a secret; it ships in the website by design. Sends staff to SVA's own Microsoft sign-in page |
| `api/env.yaml` | `AUTO_PROVISION_SSO=true`, `ALLOWED_EMAIL_DOMAINS=` | Already set. Domains is an optional extra guard, e.g. `socialventures.org.au`; empty accepts whatever SVA's directory issues |

**Publishing the tenant ID** — every merge to `main` rebuilds and publishes the website from `frontend/.env.production`, so editing that file on GitHub is all it takes. Do F2 first, or the button shows "Microsoft sign-in is not enabled for this project yet".

1. Open [frontend/.env.production](https://github.com/UtkristaUwa/CITS3200_57/blob/main/frontend/.env.production) on GitHub. After the repository moves to SVA, the link redirects.
2. Click the pencil icon, **Edit this file**.
3. On the line `VITE_ENTRA_TENANT_ID=`, paste the tenant ID straight after the `=`, with no spaces or quotation marks. Leave every other line unchanged.
4. Click **Commit changes...**, enter `Set Entra tenant ID`, choose **Create a new branch for this commit and start a pull request**, click **Propose changes**, then **Create pull request**.
5. Click **Merge pull request**, then **Confirm merge**.
6. On the [Actions](https://github.com/UtkristaUwa/CITS3200_57/actions) tab, wait for **Deploy to Firebase Hosting on merge** to show a green tick (a few minutes). A red cross means the website was not updated.
7. Test on the live site, [tenderai-dev-f0283.firebaseapp.com](https://tenderai-dev-f0283.firebaseapp.com), after a hard refresh (Ctrl+Shift+R). Not on the pull request's preview link: preview addresses are not approved for sign-in.

> **Redeploying the API:** only needed if an `api/env.yaml` setting changes. `gcloud run deploy --env-vars-file env.yaml` replaces every setting on the service with the file's contents, so the file must also carry `RUNTIME_CONFIG_BUCKET` and `RUNTIME_CONFIG_OBJECT`. If it doesn't, the redeploy removes them and the **Reference / config** page stops loading.

### F4 — Test plan

| # | Test | Expected |
| --- | --- | --- |
| 1 | On the live site, click **Sign in with Microsoft** | SVA-branded Microsoft sign-in page, not a generic one |
| 2 | Complete sign-in as an SVA staff member who has never used TenderAI | Lands on the tenders page; Firestore gains `users/{uid}` with `autoProvisioned: true`, `isAdmin: false`, `status: "active"` |
| 3 | Sign in again as the same person | No duplicate record, no change to `isAdmin` |
| 4 | An existing invited password account signs in | Still works, unchanged |
| 5 | A personal `@outlook.com` account | Rejected by Microsoft before Firebase is reached |
| 6 | With **Assignment required? = Yes**, a staff member who is not assigned | Microsoft says the user is not assigned to the app |
| 7 | `curl $API/tenders` with no header | `401 missing bearer token` |
| 8 | `curl $API/tenders -H "Authorization: Bearer garbage"` | `401 invalid token` |
| 9 | From an existing admin account, turn on the admin switch for the new Microsoft user | Admin menu appears for them after reload; the switch on your own row stays disabled |
| 10 | Leave the tab open for over an hour, then load tenders | Silent token refresh, no visible error |

### F5 — Day to day

| Task | How |
| --- | --- |
| Give someone access | With **Assignment required? = Yes**, add them under the enterprise app's **Users and groups**. With **No**, any SVA account can already sign in |
| Remove someone's access | Remove them from **Users and groups**, or disable their SVA account. A session already open is not ended by this; to cut access straight away, set `status: "disabled"` on their `users/{uid}` record in Firestore |
| Make someone an admin | They sign in once, then an existing admin turns on their switch under **Admin > User management**. Nobody is made an admin automatically |
| Renew the client secret (yearly) | Create a new secret as in F1 step 2. Paste it in the Firebase console under **Authentication > Sign-in method > Microsoft** and save; as Owner you can do this yourself. Once sign-in works, delete the old secret in Entra |
| Limit sign-in to one email domain | Optional: set `ALLOWED_EMAIL_DOMAINS=socialventures.org.au` in `api/env.yaml` and redeploy the API (see the callout in F3) |

### F6 — Troubleshooting

| What the user sees | Cause | Fix |
| --- | --- | --- |
| Microsoft error `AADSTS50011` (redirect URI mismatch) | The redirect URI in the app registration is wrong | Set it to exactly the address in F1 |
| "Microsoft sign-in is not enabled for this project yet" | Microsoft provider not switched on in Firebase | Complete F2 |
| "This site is not on the list of domains approved for sign-in" | The site address is missing from Firebase's authorised domains | Add it under **Authentication → Settings → Authorized domains** |
| "That email already has a TenderAI password account" | The person was invited with a password before Microsoft sign-in | They keep using email and password |
| Microsoft says the account is not in the tenant | A personal or non-SVA account was used | Sign in with the SVA work account |
| Microsoft says the user is not assigned to the app | **Assignment required?** is Yes and they are not assigned | Add them under **Users and groups** |
| Microsoft sign-in suddenly fails for everyone | The client secret has expired | Renew it (F5) |

---

## Part G — Things outside Google Cloud

These do not move when you become Owner of the project. Each needs its own handover.

| Item | Why it matters | What you do |
| --- | --- | --- |
| Code repository on GitHub (`UtkristaUwa/CITS3200_57`) | Holds all the source code. Merging a change publishes a new version of the website automatically | Give the team the SVA GitHub account or organisation to receive it. Accept the transfer request GitHub emails you. Then ask the team to confirm a website deployment still runs from the new location |
| Tender portal logins | AusTender, GrantConnect, Tenders ACT, NT QTOL, QLD QTenders and Buying for Victoria only release documents to a signed-in account. If an account belongs to a student and is closed, the tender text is still collected but its documents are not | Ask the team which email address each portal account is registered to. Register SVA accounts for any that are not SVA's and give the new details to the team to store in the project |
| Microsoft Entra app registration | Lives in SVA's own directory, so SVA already owns it. Its client secret expires after 12 or 24 months | Set it up with Part F. Keep the expiry date in a calendar |
| Custom web address, if SVA wants one | The site uses the address Firebase gave it. A domain name is registered separately | Nothing unless SVA wants its own address; then SVA IT registers it and a developer connects it |

---

## Part H — Running TenderAI as owner

Day to day you work in the TenderAI Admin pages. The Google Cloud console is for money and access, and the Entra admin center for Microsoft sign-in. You should rarely need either.

**How the system works.** Every day at 5 am Perth time, with nobody starting it, the pipeline works through seven portals: GrantConnect, buy.nsw, Tenders ACT, AusTender, NT QTOL, QLD QTenders and Buying for Victoria.

1. It reads each portal's open tenders and downloads each tender's page and documents.
2. It extracts the text from the documents and stores the originals.
3. Google Gemini, running inside the project, summarises each tender, pulls out the key fields, and scores its relevance to SVA using the focus areas, work types and out-of-scope list you set.
4. It saves the result to the database. A tender seen before is updated, not duplicated.
5. It publishes a health report for each portal, and emails the alert address if anything failed.

Staff then sign in, search and filter tenders, mark favourites, open the original documents, and share a tender by email from their own mail program.

| Admin page | What it shows | What you can do |
| --- | --- | --- |
| User management | Everyone with a TenderAI account: email, role, status (Active or Pending) and date invited | Invite a person by email, with **Make admin** ticked if needed. Turn the admin switch on or off for anyone except yourself |
| System / ingestion health | One row per portal from the latest run: last run, status and message | Check the pipeline. **Success**: the portal was read. **Failed to download**: tenders collected but some documents not fetched, usually a portal login. **Error**: the portal could not be read at all |
| Reference / config | The AI settings the pipeline uses | Change the two AI models, the classification guidance, the focus-area and work-type lists, the out-of-scope list and the scoring weights. Changes apply from the next daily run and do not re-score existing tenders |

> **When an alert email arrives:** open **Admin > System / ingestion health** and find the row that is not Success. A single **Failed to download** or a one-off **Error** often clears on the next run. The same portal failing for several days means its site or login has changed, and a developer needs to look.

| Task | Where |
| --- | --- |
| Invite a user, or make someone an admin | TenderAI, **Admin > User management** |
| Check that yesterday's run worked | TenderAI, **Admin > System / ingestion health** |
| Change what counts as relevant, or which AI models are used | TenderAI, **Admin > Reference / config** |
| Change who receives failure alert emails | Google Cloud console, **Monitoring > Alerting** (Part D) |
| See what the system costs | Google Cloud console, **Billing > Reports** |
| Give or remove access to the cloud project | Google Cloud console, **IAM & Admin > IAM** |
| Give or remove a staff member's Microsoft sign-in | Entra admin center, TenderAI enterprise app, **Users and groups** (F5) |
| Renew the Microsoft client secret | Entra admin center, then Firebase console (F5) |

**Three habits that keep you out of trouble**

- Keep two Owners on the project and two admins in TenderAI at all times. When one leaves SVA, add the replacement before removing the leaver.
- Read the alert and budget emails. A sudden jump in cost usually means the daily run is processing far more than normal.
- Every six months, review the IAM list and the User management page and remove access nobody needs.

**If SVA ever stops using TenderAI:** export what you want to keep from the database first. Then in the console go to **IAM & Admin > Settings** and choose **Shut down**. Charges stop, and the project can be restored for 30 days before it is deleted for good.

---

## Part I — Known gaps at handover

1. **System / ingestion health shows "Failed to fetch" on the live site.** The page reads `scraper_health.json` straight from the document bucket and gets a 403. Fix by serving it through the API; do **not** make the bucket public, because it also holds every tender document. *[Team to fix before handover.]*
2. **Reference / config shows "Unable to load" on the live site.** The live API is an older build without the `/admin/config` routes. It needs a redeploy from `main`, the config file in a bucket, and `RUNTIME_CONFIG_BUCKET` and `RUNTIME_CONFIG_OBJECT` set on both `tenderai-api` and `tender-batch-job`. *[Team to fix before handover.]*
3. **Invites show a setup link on screen, and it points to `http://localhost:5173`** (`functions/src/index.ts`), so it does not work on the live site. No email is sent. *[Team to fix before handover.]*
4. **`firestore.rules` must be deployed** with `firebase deploy --only firestore:rules`. Until it is, a user could write their own `isAdmin` flag, because favourites and `isAdmin` share one record. *[Team to confirm it is deployed.]*
5. **`tenderai-api` allows `allUsers` to call it — deliberately.** Cloud Run's own access check expects Google-issued tokens, and Firebase tokens are not those; removing it breaks the website. The API's own token check is the real gate.
6. **The portals are fixed in the code.** Adding or removing one needs a developer.
7. **The tender extraction prompt** on the config page is display only.
8. **There is no Microsoft Teams notification.** The only automatic message is the failure alert email.
9. **Users cannot be removed or disabled from the Admin pages**, only have admin rights turned off. Use Firestore, as in Part C step 10.
10. **The Entra client secret expires** 12 or 24 months after it is created (Part F).

---

## Part J — Optional: moving the project into an SVA Google Cloud organisation

Not needed for the handover; worth raising with SVA IT afterwards. A project created by individuals sits under "No organisation", so it belongs to whichever accounts are listed as Owner. Moving it into an SVA organisation makes SVA itself the top-level owner.

| Topic | Details |
| --- | --- |
| What you gain | SVA IT can always recover access, even if every named owner leaves, and SVA's security policies apply |
| What it needs | An SVA Google Cloud organisation (from Google Workspace, or the free Cloud Identity service on SVA's domain), and the **Project Creator** role on it for you |
| How | **IAM & Admin > Manage resources**, find `tenderai-dev` under "No organisation", three-dot menu, **Migrate**, select the SVA organisation |

> **Cautions:** it is one-way; moving back needs Google support. SVA's organisation policies apply straight away, and one restricting access to SVA accounts would lock out any remaining student accounts. Billing does not move with it. Do it only after Part E passes, with the team or SVA IT available, and run Part E again afterwards.

---

## Contacts and further reading

**The student team** (UWA CITS3200, Group 57). Support is available until [end date to be agreed].

| Area | Contact |
| --- | --- |
| Cloud project, deployment, sign-in | [name, email] |
| Website and user interface | [name, email] |
| Database and API | [name, email] |
| Source scanning and ingestion | [name, email] |
| AI matching and admin page | [name, email] |

**Technical documentation** in the code repository, for any developer SVA brings in: `README.md` (overview), `DEVELOPMENT.md` (setup, sign-in, admin users, deployment, known gaps), `docs/SSO_SETUP.md` (Microsoft sign-in), `error_scrapers/README.md` (portal scrapers and status codes), `Schema/` (database structure) and `manager.py` (the daily pipeline).

**Google's and Microsoft's own instructions:**

- [Granting and removing access to a project](https://docs.cloud.google.com/iam/docs/granting-changing-revoking-access)
- [Changing the billing account for a project](https://docs.cloud.google.com/billing/docs/how-to/modify-project)
- [Managing alert notification channels](https://docs.cloud.google.com/monitoring/support/notification-options)
- [Registering an app in Microsoft Entra ID](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)
- [Adding a client secret](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-credentials)
- [Moving a project with no organisation into an organisation](https://docs.cloud.google.com/resource-manager/docs/handle-special-cases)

---

## Appendix — Messages to send Ramon

**Before the handover call**

> Hi Ramon — to hand TenderAI over we need three things from SVA before the call: a Google account SVA controls (please send us its exact email address), a second SVA person to be backup owner, and an SVA Cloud Billing account on which you are Billing Account Administrator. On the call we add you as Owner of `tenderai-dev`; you accept the email invitation, move billing to SVA, and we run the checks together before removing our access. It takes about 30 minutes. The full steps are in Parts B to E of the Owner Manual.

**Microsoft sign-in**

> Hi Ramon — to add Microsoft sign-in to TenderAI we need an app registration in SVA's Entra tenant. It's a short job for whoever administers your Microsoft 365 tenant:
>
> - **New app registration**, name `TenderAI (UWA CITS3200)`
> - **Single tenant** (SVA directory only)
> - **Redirect URI (Web):** `https://tenderai-dev-f0283.firebaseapp.com/__/auth/handler`
> - **A client secret**, 12-month expiry
> - Delegated Graph permissions `openid`, `email`, `profile`, `User.Read`, with admin consent granted
>
> We then need the **Application (client) ID**, the **Directory (tenant) ID**, and the **client secret value**. The secret should come through a secure channel, not email; it only ever gets stored in Firebase, never in our code.
>
> If you'd rather not open it to the whole directory, set **Assignment required = Yes** on the enterprise app and assign just the people who should have access — nothing changes on our end for that. Step by step, it's Part F1 of the Owner Manual.
