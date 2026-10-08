# TenderAI Owner Manual

*Prepared by the UWA CITS3200 team (Group 57) for Social Ventures Australia · 8 October 2026*

## About this manual

This manual is for the person at SVA who owns TenderAI after the UWA CITS3200 team (Group 57) hands it over. Today that is Dr Ramon Wenzel. It covers what you are taking over, the steps you take to become the owner of the Google Cloud project, the checks to run afterwards, and what owning the system involves month to month.

It is written for an owner, not a developer. Nothing here needs code or a terminal; every step is done in a web browser. How the system is built, and how to change it, is in the technical documentation in the code repository.

## What you are taking over

TenderAI is one Google Cloud project plus a few things that sit beside it. Almost everything lives inside the project, so becoming its Owner hands you most of the system in one step.

| Item | What it is | Where your steps are |
| --- | --- | --- |
| Google Cloud project `tenderai-dev` | The container for the whole system. Everything below except the last three rows lives inside it, in Google's Sydney region | Taking ownership, steps 1 to 3 |
| Billing | The account Google charges for the project's running costs. Today it is a team member's account | Steps 4 and 5 |
| Tender database | BigQuery dataset `TenderAI`. Holds every tender the system has collected | Comes with the project |
| Document storage | Cloud Storage bucket `tenderai-dev-documents`. Holds the original tender attachments and the daily health report | Comes with the project |
| Daily pipeline | A scheduled job, `tender-batch-job`, that collects and processes tenders at 5 am Perth time every day | Comes with the project |
| Website and API | The website is on Firebase Hosting at `tenderai-dev-f0283.firebaseapp.com`. It talks to a backend service called `tenderai-api` | Step 7 |
| User accounts | Firebase holds the list of users, who is an admin, and each person's favourites | Step 8 |
| Failure alert emails | Google Cloud emails a named person when the daily pipeline reports an error. Today that is a team member | Step 9 |
| Microsoft sign-in | An app registration in SVA's own Microsoft Entra tenant. Not switched on yet | Setting up Microsoft sign-in |
| Tender portal logins | Usernames and passwords the pipeline uses to download documents from portals that need an account | Things outside Google Cloud |
| Code repository on GitHub | The source code. Merging a change there publishes a new version of the website | Things outside Google Cloud |

## Before you start

Have these four things ready. The transfer itself then takes about 30 minutes and is easiest done on a call with the student team.

- **A Google account that SVA controls.** Ownership attaches to a Google account, so avoid a personal Gmail address. If SVA has Google Workspace or Cloud Identity, use that account. If not, create a Google account with your SVA email address (choose "Use my current email address" when signing up). Turn on 2-Step Verification.
- **A second SVA person as backup owner.** If the only owner leaves or loses the account, the project is very hard to recover. Pick the person now and have them set up a Google account the same way.
- **An SVA Cloud Billing account.** This is a payment profile in Google Cloud, with a card or invoicing, on which you are Billing Account Administrator. Create one at console.cloud.google.com/billing if SVA does not have one. Skip this if the project already bills to SVA.
- **The exact email address of your Google account, sent to the team.** They use it to invite you. A typo or an alias means the invitation goes to an account you cannot sign in with.

## Taking ownership of the Google Cloud project

There is no "transfer" button in Google Cloud. Ownership moves in three stages: the team adds you as an Owner, you move billing and settings to SVA, and then you remove the team. Do the steps in this order, and do not remove the team (step 10) until billing is on SVA's account and the checks in "Checks after the transfer" pass.

1. **Send the team your Google account email.** The team then grants you the Owner role on `tenderai-dev`. This is their only action; everything below is yours.
2. **Accept the invitation.** Google emails you an invitation to join the project as Owner. Open it while signed in to the same Google account and accept. You are not an owner until you accept.
   - No email after a few minutes? Check spam, then ask the team to confirm the address they entered.
   - Still nothing? Sign in at console.cloud.google.com and look for the invitation in the notifications bell, or ask the team to send you the invitation link directly.
3. **Confirm you are an Owner.** Go to console.cloud.google.com, choose `tenderai-dev` in the project picker at the top, then open **IAM & Admin > IAM**. Your email should be listed with the role **Owner**. New access can take a few minutes to show.
4. **Move billing to SVA.** Open **Billing**, find `tenderai-dev` in the list of projects, open the three-dot **Actions** menu on its row, choose **Change billing**, select the SVA billing account and click **Set account**.
   - You need to be Owner of the project (you now are) and Billing Account Administrator or User on the SVA billing account.
   - Costs incurred before the switch stay on the old account. Costs after it go to SVA. Nothing switches this automatically: handing over ownership or removing the team leaves the project on the team member's billing account until you change it here.
5. **Set a budget alert.** In **Billing > Budgets & alerts**, create a budget for `tenderai-dev` with a monthly amount you are comfortable with, and email alerts at 50%, 90% and 100%. A budget only warns you. It does not stop spending.
6. **Add your backup owner.** In **IAM & Admin > IAM**, click **Grant access**, enter the second SVA person's Google account, choose the role **Owner** and click **Save**. They accept an invitation the same way you did.
7. **Check Firebase.** Sign in at console.firebase.google.com with the same Google account. The `tenderai-dev` project should appear, and **Project settings > Users and permissions** should show you as Owner. Nothing to change here; this confirms the website, user accounts and sign-in came across with the project.
8. **Confirm your admin rights in TenderAI.** Your TenderAI account is already an admin. Sign in to the website and check that **Admin** appears in the top menu.
   - Your backup needs to be an admin too. Once they have a TenderAI account, turn on the switch in the Role column on their row under **Admin > User management**.
   - Do this before the team leaves. An admin cannot remove their own admin rights, which protects you from locking yourself out, but it also means only another admin can make new ones.
9. **Move the failure alert emails to SVA.** When the daily pipeline finishes with an error, Google Cloud sends an email headed "Log alert fired" from the policy **Pipeline finished, Scraper error**. It currently goes to a team member. The full steps are in "Moving the failure alert emails", straight after this list.
   - In the Google Cloud console open **Monitoring > Alerting**, then **Edit notification channels**. Add your email address, and your backup's, under Email.
   - Open the policy **Pipeline finished, Scraper error**, click **Edit**, and under notifications select the SVA channels and remove the team member's.
10. **Remove the team's access.** Only after the checks in "Checks after the transfer" pass, and only once the Billing page shows `tenderai-dev` on SVA's billing account (step 4). Removing a team member does not move billing: the project would keep charging their account, and they could no longer see it.
    - In **IAM & Admin > IAM**, for each student account click **Edit principal**, delete every role and click **Save**.
    - In TenderAI, under **Admin > User management**, turn off the admin switch on every team account. The page has no remove or disable button, so ask the team to disable their own accounts in the Firebase console before they go.
    - Leave any entry ending in `gserviceaccount.com` alone. These are not people; they are the identities the system itself runs under.
    - If you have agreed a support period, keep one team member on a reduced role such as Editor until it ends, then remove them.

## Moving the failure alert emails

The failure email is a Google Cloud Monitoring alert policy called **Pipeline finished, Scraper error**. It emails everyone on its notification channels whenever the daily run ends with an error. Today that is one team member. Moving it to SVA takes about ten minutes.

1. **Open Alerting.** Go to console.cloud.google.com/monitoring/alerting and choose `tenderai-dev` in the project picker at the top.
2. **Add the SVA email addresses.** Click **Edit notification channels**. In the **Email** section click **Add new**, enter your email address and a description such as "Ramon (SVA)", and click **Save**. Repeat for your backup.
   - A shared mailbox or group address works too. Set it to accept mail from `alerting-noreply@google.com`.
3. **Open the policy.** Go back to **Alerting** and click **See all policies**. Find **Pipeline finished, Scraper error**, click the three-dot **More options** menu on its row and choose **Edit**.
4. **Change who it notifies.** Go to the **Notifications and name** section. In the notification channels list, tick the SVA channels you added and untick the team member's. Click **Save policy**.
5. **Check for other policies.** While the policy list is open, look for any other policy that notifies a team member, and change it the same way.
6. **Make sure the emails get through.** Add `alerting-noreply@google.com` to your Outlook safe senders. Outlook blocks parts of these emails from unknown senders, including the **View incident** button.
7. **Tidy up after handover.** The console cannot delete a channel while a policy still uses it. Once no policy lists the team member's channel, open **Edit notification channels**, find it under Email and click **Delete**.

The next run that reports an error confirms it works: the email should reach the SVA address. Each email lists the error codes from 0 to 6. The **System / ingestion health** page shows which portal failed, so start there.

## Checks after the transfer

Run these after step 9, with the team still on the call. If any fail, the team can still fix them because their access has not been removed yet.

- [ ] **IAM & Admin > IAM** lists you and your backup as Owner.
- [ ] **Billing** shows `tenderai-dev` linked to the SVA billing account, and the budget alert exists.
- [ ] The alert policy **Pipeline finished, Scraper error** lists an SVA email address.
- [ ] You can open the TenderAI website and sign in with your own account.
- [ ] **Admin > User management** opens and shows you and your backup as admins.
- [ ] **Admin > System / ingestion health** shows a Last Run from this morning for each website.
- [ ] The Home page shows tenders, including some published in the last few days.

After step 10, run the last four checks again the next day. That confirms nothing was quietly depending on a student's account.

## Optional: moving the project into an SVA Google Cloud organisation

This is not needed for the handover, and it is worth raising with SVA IT afterwards. A project created by individuals usually sits under "No organisation", which means it belongs to whichever personal accounts are listed as Owner. Moving it into an SVA organisation makes SVA itself the top-level owner.

**What you gain:** SVA IT can always recover access, even if every named owner leaves, and SVA's security policies apply to the project.

**What it needs:** SVA must have a Google Cloud organisation, which comes with Google Workspace or with the free Cloud Identity service set up on SVA's domain. SVA's organisation administrator grants you the **Project Creator** role on the organisation.

**How you do it:** in the console, open **IAM & Admin > Manage resources**, find `tenderai-dev` under "No organisation", open its three-dot menu, choose **Migrate** and select the SVA organisation.

**Cautions before you click:**

- It is one-way. Moving a project back to "No organisation" needs Google support.
- SVA's organisation policies start applying straight away. A policy that restricts access to SVA accounts only would lock out any remaining student accounts.
- Billing does not move with the project. It stays on whichever billing account you set in step 4.
- Do it only after the checks above pass, with the team or SVA IT available, and run the checks again afterwards.

## Things outside Google Cloud

These do not move when you become Owner of the project. Each needs its own handover.

| Item | Why it matters | What you do |
| --- | --- | --- |
| Code repository on GitHub (`UtkristaUwa/CITS3200_57`) | Holds all the source code. Merging a change into it publishes a new version of the website automatically | Give the team the SVA GitHub account or organisation to receive it. Accept the transfer request GitHub emails you. Then ask the team to confirm a website deployment still runs from the new location |
| Tender portal logins | AusTender, GrantConnect, Tenders ACT, NT QTOL, QLD QTenders and Buying for Victoria only release tender documents to a signed-in account. The pipeline signs in with a username and password for each. If an account belongs to a student and is closed, the tender text is still collected but its documents are not | Ask the team which email address each portal account is registered to. Register SVA accounts for any that are not SVA's and give the new details to the team to store in the project |
| Microsoft Entra app registration | Lets staff sign in with their SVA Microsoft account. It lives in SVA's Microsoft tenant, so SVA already owns it. Its client secret expires after 12 or 24 months, and Microsoft sign-in stops working that day | Put the expiry date in a calendar. Follow "Setting up Microsoft sign-in" below to set it up and to renew it |
| Custom web address, if SVA wants one | The site currently uses the address Firebase gave it. A domain name is registered separately from Google Cloud | Nothing unless SVA wants its own address; then SVA IT registers it and a developer connects it |

## Setting up Microsoft sign-in

SVA already has a Microsoft Entra directory: it is what SVA's Microsoft 365 work accounts sign in through. What is missing is TenderAI's registration in it. Once that exists, staff can sign in to TenderAI with their SVA Microsoft account. Steps 1 to 7 take about 15 minutes in the Microsoft Entra admin center; step 8 is done by a developer.

**Who can do it.** You need the Entra role **Cloud Application Administrator**, **Application Administrator** or Global Administrator. A normal staff account can register the app but cannot complete steps 5 and 6. If you don't hold one of these roles, ask SVA's Microsoft 365 administrator to do it, or to sit with you while you do.

1. **Sign in.** Go to entra.microsoft.com and sign in with your SVA work account. If you belong to more than one directory, use the **Settings** icon in the top bar to switch to SVA's.
2. **Register TenderAI.** Go to **Entra ID > App registrations** and click **New registration**. Fill in:
   - **Name:** `TenderAI`
   - **Supported account types:** **Single tenant only**, meaning SVA's directory only. Only SVA accounts can then sign in.
   - **Redirect URI:** platform **Web**, value `https://tenderai-dev-f0283.firebaseapp.com/__/auth/handler`. Copy it exactly; the `-f0283` matters.

   Click **Register**.
3. **Copy the two IDs.** On the app's **Overview** page, copy the **Application (client) ID** and the **Directory (tenant) ID**. These are identifiers, not secrets.
4. **Create the client secret.** Go to **Certificates & secrets > Client secrets > New client secret**. Enter the description `Firebase sign-in` and choose an expiry: 12 months is Microsoft's recommendation, and 24 months is the longest allowed. Click **Add**, then copy the **Value** column straight away.
   - The value is never shown again after you leave the page.
   - The **Secret ID** column is not the secret.
   - Put the expiry date in your calendar with a reminder a month before. Microsoft sign-in stops working the day it expires.
5. **Check the permissions.** Go to **API permissions**. Microsoft Graph should list `openid`, `email`, `profile` and `User.Read`, all Delegated. Add any that are missing with **Add a permission > Microsoft Graph > Delegated permissions**. Then click **Grant admin consent for SVA** and confirm.
6. **Choose who can sign in.** This step is recommended. Go to **Entra ID > Enterprise apps > All applications** and open **TenderAI**.
   - Under **Properties**, set **Assignment required?** to **Yes** and click **Save**.
   - Under **Users and groups**, click **Add user/group**, select the staff who should have access, and click **Assign**.
   - Assigning a whole group needs an Entra ID P1 or P2 licence. On the free tier, add people one at a time.
   - If you leave **Assignment required?** set to **No**, anyone with an SVA account can sign in.
7. **Hand the details to the developer.** Email the client ID and tenant ID. Send the secret value through a password manager share, or read it out on a call. Never send it by email or chat.
8. **The developer switches it on.** In the Firebase console, go to **Authentication > Sign-in method > Add new provider > Microsoft**. Turn it on, paste the client ID and secret, and save. Then add the tenant ID to the website, as in "Adding the tenant ID to the website" below. The full technical checklist is `docs/SSO_SETUP.md` in the repository.

**Adding the tenant ID to the website** (developer, about 5 minutes, all in the browser)

The tenant ID lives in the file `frontend/.env.production` in the repository. It is not a secret; it is meant to be visible in the website. Every merge to `main` rebuilds and publishes the website with whatever that file says, so changing the file is all it takes. Do the Firebase part of step 8 first, or the button shows "Microsoft sign-in is not enabled for this project yet".

1. Open the file on GitHub: [frontend/.env.production](https://github.com/UtkristaUwa/CITS3200_57/blob/main/frontend/.env.production). After the repository moves to SVA, this link redirects to the new location.
2. Click the pencil icon, **Edit this file**.
3. Find the line `VITE_ENTRA_TENANT_ID=`. Paste the Directory (tenant) ID straight after the `=`, with no spaces or quotation marks. Leave every other line unchanged.
4. Click **Commit changes...**, enter the message `Set Entra tenant ID`, choose **Create a new branch for this commit and start a pull request**, and click **Propose changes**, then **Create pull request**.
5. Click **Merge pull request**, then **Confirm merge**.
6. Open the [Actions](https://github.com/UtkristaUwa/CITS3200_57/actions) tab and wait for the **Deploy to Firebase Hosting on merge** run to show a green tick. This takes a few minutes. A red cross means the website was not updated; open the run to see why.
7. Test on the live site, [tenderai-dev-f0283.firebaseapp.com](https://tenderai-dev-f0283.firebaseapp.com), after a hard refresh (Ctrl+Shift+R). Don't test on the preview link the pull request posts: preview addresses are not approved for sign-in, so Microsoft sign-in fails there by design.

**Test it.** Open TenderAI and click **Sign in with Microsoft**. You should see SVA's own Microsoft sign-in page and then land on the tenders page. A person's first Microsoft sign-in makes them an ordinary user; to make them an admin, use **Admin > User management**.

**Renewing the secret each year.** Repeat step 4 to create a new secret. Then, as Owner of the project, paste it in yourself: Firebase console, **Authentication > Sign-in method > Microsoft**, replace the secret and save. Once sign-in works with the new secret, delete the old one under **Certificates & secrets**. No developer is needed for this.

| If you see | Cause | Fix |
| --- | --- | --- |
| Microsoft error AADSTS50011 | The redirect URI does not match | Set it to exactly the address in step 2 |
| Microsoft says the user is not assigned to the app | **Assignment required?** is Yes and the person is not assigned | Add them under **Users and groups** (step 6) |
| Microsoft sign-in suddenly fails for everyone | The client secret has expired | Renew it as above |
| "That email already has a TenderAI password account" | The person was invited with a password earlier | They keep signing in with email and password |

## Running TenderAI as owner

Day to day you work in the TenderAI Admin pages. The Google Cloud console is for money and access, and the Azure portal is for Microsoft sign-in once that is switched on. You should rarely need either.

**How the system works**

Every day at 5 am Perth time, with nobody needing to start it, the pipeline does the following for each of seven portals: GrantConnect, buy.nsw, Tenders ACT, AusTender, NT QTOL, QLD QTenders and Buying for Victoria.

1. It reads the portal's open tenders and downloads each tender's page and documents.
2. It extracts the text from the documents and stores the originals.
3. An AI model (Google Gemini, running inside the project) summarises each tender, pulls out the key fields, and scores how relevant it is to SVA using the focus areas, work types and out-of-scope list you set.
4. It saves the result to the database. A tender it has seen before is updated, not duplicated.
5. It publishes a health report for each portal, and emails the alert address if anything failed.

Staff then sign in to the website, search and filter the tenders, mark favourites, open the original documents, and share a tender by email from their own mail program.

**The Admin pages**

| Page | What it shows | What you can do |
| --- | --- | --- |
| User management | Everyone with a TenderAI account: email, role, status (Active or Pending) and the date invited | Invite a person by email, with **Make admin** ticked if needed. Turn the admin switch on or off for anyone except yourself |
| System / ingestion health | One row per portal from the latest run: when it last ran, a status and a message | Nothing to change; this is where you check the pipeline. **Success** means the portal was read. **Failed to download** means tenders were collected but some documents could not be fetched, usually because of a portal login. **Error** means the portal could not be read at all |
| Reference / config | The AI settings the pipeline uses | Change the two AI models used for sorting documents and for summarising. Change how relevance is judged: the classification guidance, the focus-area and work-type lists, the out-of-scope list, and the scoring weights. Changes apply from the next daily run and do not re-score tenders already collected |

**What to do when an alert email arrives.** Open **Admin > System / ingestion health** and find the row that is not Success. A single **Failed to download** or a one-off **Error** often clears on the next run. The same portal failing for several days means the portal has changed its site or its login has stopped working, and a developer needs to look.

**Known limits at handover**

- **System / ingestion health** currently shows "Failed to fetch" on the live site, so the daily status cannot be read there yet. \[Team to fix before handover.\]
- **Reference / config** currently shows "Unable to load" on the live site, so the AI settings cannot be viewed or changed there yet. \[Team to fix before handover.\]
- Inviting a user shows a setup link on screen; no email is sent. That link currently points to a developer's test address and does not work on the live site. \[Team to fix before handover, or confirm the workaround.\]
- The portals are fixed in the code. Adding or removing one needs a developer; there is no admin page for it.
- The tender extraction prompt on the config page is display only and cannot be edited there.
- There is no Microsoft Teams notification. The only automatic message is the failure alert email to the owner.
- A user cannot be removed or disabled from the Admin pages, only have admin rights turned off.

**Where each task is done**

| Task | Where |
| --- | --- |
| Invite a user, or make someone an admin | TenderAI, **Admin > User management** |
| Check that yesterday's run worked | TenderAI, **Admin > System / ingestion health** |
| Change what counts as relevant, or which AI models are used | TenderAI, **Admin > Reference / config** |
| Change who receives failure alert emails | Google Cloud console, **Monitoring > Alerting** |
| See what the system costs | Google Cloud console, **Billing > Reports** |
| Give or remove a person's access to the cloud project | Google Cloud console, **IAM & Admin > IAM** |
| Give or remove a staff member's Microsoft sign-in, once it is switched on | Azure portal, the TenderAI enterprise application, **Users and groups** |
| Renew the Microsoft client secret before it expires | Create it in the Azure portal, then paste it in the Firebase console under **Authentication > Sign-in method > Microsoft** |

**Three habits that keep you out of trouble**

- Keep two Owners on the project and two admins in TenderAI at all times. When one leaves SVA, add the replacement before removing the leaver.
- Read the alert and budget emails. A sudden jump in cost usually means the daily run is processing far more than normal and is worth a developer's look.
- Review the IAM list and the User management page every six months and remove access nobody needs any more.

**If SVA ever stops using TenderAI:** export what you want to keep from the database first. Then in the console go to **IAM & Admin > Settings** and choose **Shut down**. Charges stop, and the project can be restored for 30 days before it is deleted for good.

## Contacts and further reading

**The student team** (UWA CITS3200, Group 57). Support is available until \[end date to be agreed\].

| Area | Contact |
| --- | --- |
| Cloud project, deployment, sign-in | \[name, email\] |
| Website and user interface | \[name, email\] |
| Database and API | \[name, email\] |
| Source scanning and ingestion | \[name, email\] |
| AI matching and admin page | \[name, email\] |

**Technical documentation.** How the system is built and how to change it lives in the code repository. Hand these to any developer SVA brings in later: `README.md` (overview), `DEVELOPMENT.md` (setup, sign-in, admin users, deployment and known gaps), `docs/SSO_SETUP.md` (Microsoft sign-in), `error_scrapers/README.md` (the portal scrapers and their status codes), `Schema/` (database structure) and `manager.py` (the daily pipeline itself).

**Google's own instructions** for the steps in this manual:

- [Granting and removing access to a project](https://docs.cloud.google.com/iam/docs/granting-changing-revoking-access)
- [Changing the billing account for a project](https://docs.cloud.google.com/billing/docs/how-to/modify-project)
- [Moving a project with no organisation into an organisation](https://docs.cloud.google.com/resource-manager/docs/handle-special-cases)