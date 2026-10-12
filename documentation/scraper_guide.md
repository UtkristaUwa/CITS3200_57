# TenderAI Scraper Guide

How to add a tender-website scraper, keep it healthy, and fix it.

| Guide           | Details                                                                                                                                                                                                                                   |
|-----------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **For**         | Dr Ramon Wenzel and the SVA team who look after TenderAI                                                                                                                                                                                  |
| **Prepared by** | Benjamin Gilmore, TenderAI student team (CITS3200), Sprint 3                                                                                                                                                                              |
| **Version**     | 3.4, 12 October 2026                                                                                                                                                                                                                      |
| **Applies to**  | The `main` branch at commit 78138c4 (12 October 2026) or later, which includes all of the 12 October scraper fixes; 11 scrapers. The code is the source of truth: if it disagrees with this guide, follow the code and correct the guide. |

# 1. The basics

**How it works.** Every morning at 5 am Perth time, Cloud Scheduler starts the Cloud Run job `tender-batch-job`. It runs `manager.py`, which runs each scraper in its `SCRAPERS` list in turn. A scraper leaves one folder per tender (page text plus any documents) and returns a status code. The pipeline then stores the documents in Cloud Storage, has Gemini summarise each tender and saves it to BigQuery (a tender seen before is updated, not duplicated). After each website the health report `scraper_health.json` is rewritten; the System Health page shows it. The job ends as **failed** if any scraper reported an error code (1 to 5), if a tender hit an unexpected error while being stored, or if nothing at all was scraped. A Partial (code 6) does not fail the job.

**What you need.** A Google account with access to the `tenderai-dev` project (you will use Google Cloud Shell); write access to the GitHub repository `UtkristaUwa/CITS3200_57`; a computer with Python 3.12, git, Google Chrome and the `gcloud` CLI; and an AI coding assistant that can read and edit files and run commands inside your copy of the repository (“a new chat in the repository folder” means that).

**Who does what.** You decide what to add or fix, save web pages, type passwords (only into Cloud Shell prompts or the local `.env` file) and run the Cloud Shell commands. The AI reads the repository, writes tests and code, registers the scraper, runs the local checks, commits to a branch and reports in plain English. You review and approve.

**Rules that never change**

1.  Passwords, cookies and tokens never go into the AI chat.
2.  Scrapers only read. They never lodge a response, apply for a tender or change an account.
3.  If a website blocks us, we stop and report it. No workarounds. Waiting and trying later is fine.
4.  Never merge or deploy with a scraper commented out in `manager.py` (step A6): the nightly job would skip it, and one cloud run would rewrite the health page without the other websites.
5.  `common.py`, `reporting.py` and `run_notes.py` are shared by every scraper. The AI asks before touching them.
6.  Never send less to Gemini to make a problem go away.

**Most trouble fixes itself.** The scrapers retry unreachable sites, restart dead browsers, wait out Cloudflare challenges, sign in again after a lapsed session and re-fetch tenders stored incomplete. A single Partial or Error usually clears by the next night. Act only if the message says the layout changed or the login failed, or if it repeats for three nights.

# 2. One-time setup

**2.1 Cloud Shell variables.** Open console.cloud.google.com, choose `tenderai-dev`, click the terminal icon. Paste this at the start of every Cloud Shell session (the variables vanish when it resets):

    PROJECT=tenderai-dev
    REGION=australia-southeast1
    JOB=tender-batch-job
    BUCKET=tenderai-dev-documents
    gcloud config set project "$PROJECT"

**2.2 The code, on your computer** (Python 3.12 exactly: the pinned PyMuPDF does not install on 3.13):

    git clone https://github.com/UtkristaUwa/CITS3200_57.git
    cd CITS3200_57
    python3.12 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt -r ingestion/requirements.txt -r api/requirements.txt pytest

On Windows: `py -3.12 -m venv .venv` then `.venv\Scripts\Activate.ps1`. Activate the environment in every new terminal. Install all three requirements files: with `requirements.txt` alone the test command below stops with four “error during collection” messages and runs nothing.

**2.3 Baseline tests.** Run them before you change anything and note what fails:

    python -m pytest tests error_scrapers -q

Always name both folders (plain `pytest` runs only `tests/`). The suite should pass. Two things are outside the scraper code: the API’s own tests in `tests/test_tenders_api.py`, and GrantConnect’s `test_site_login_failed_stops_the_run`, which needs the internet (offline it takes about four minutes to fail; that is not a hang). On Windows also skip the `SIGKILL` tests in `tests/test_manager.py` and add `--ignore=tests/test_extraction_timeout.py`.

**2.4 Google sign-in for local runs** (once). `manager.py` opens its BigQuery and Gemini clients at startup even for a local test:

    gcloud auth application-default login

**2.5 Local credentials (login websites only).** Create `.env` in the repository root, one line per value, for example `SA_USERNAME=...` and `SA_PASSWORD=...`. Git ignores it. Every login scraper reads it except QLD, which reads only the environment: for QLD set `QLD_USERNAME` and `QLD_PASSWORD` in the terminal.

**2.6 Brief the AI.** Start every AI chat by pasting the standing instructions (Appendix A), then the request from step A4 (new scraper) or B3 (fix).

# Part A. Build a new scraper

| Step | What                                         | Who                 |
|------|----------------------------------------------|---------------------|
| A1   | Decide what kind of website it is            | You                 |
| A2   | Choose the names                             | You                 |
| A3   | Save the website pages (fixtures)            | You                 |
| A4   | Brief the AI                                 | You                 |
| A5   | Tests and live check                         | AI (you can repeat) |
| A6   | Register in `manager.py` and test through it | AI                  |
| A7   | Create the login secrets                     | You, Cloud Shell    |
| A8   | Commit and merge                             | AI, you approve     |
| A9   | Build and deploy                             | You, Cloud Shell    |
| A10  | Smoke test and check the first run           | You, Cloud Shell    |

## A1. Decide what kind of website it is

Open it in a private browser window. Your answer decides which existing scraper the AI copies.

| Type                      | How to recognise it                                                                                       | Scrapers to copy                                                                |
|---------------------------|-----------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| Public                    | Tenders and documents open without an account.                                                            | `tenders_wa`, `buy_nsw`, `eTender` (page text only)                             |
| Login for documents       | Tender pages are public but documents ask you to sign in.                                                 | `nt_qtol`, `tenders_tas`, `austender`, `grant_connect`                          |
| Needs a real browser      | A script is refused (403, “Checking your browser”, Cloudflare), or even the login page cannot be fetched. | `vic_buyingfor`, `sa_tenders`, `tenders_act`                                    |
| List loaded by JavaScript | The saved page has no tender rows, but the live page does.                                                | `sa_tenders`, `qld_qtenders` (JSON endpoint)                                    |
| Blocks part-way           | The site stops us before every tender is read.                                                            | `buy_nsw`, `vic_buyingfor`, `sa_tenders`, `tenders_act` (these resume next run) |

> **Tip.** Buying for Victoria, Tenders ACT and SA Tenders run on the same platform. If the new website looks like one of them, say so: the AI can mirror it and finish much faster.

## A2. Choose the names

| Item         | Rule                                                                                                                                                         | Example                        |
|--------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------|
| Folder       | Lowercase with underscores, inside `error_scrapers/`.                                                                                                        | `tenders_tas`                  |
| `source_id`  | Lowercase letters, digits, underscores. Identifies the website in `manager.py`, BigQuery and the storage path. **Never change it once the scraper has run.** | `tenders_tas`                  |
| Test file    | Starts with `test_`, inside `test_cases/`.                                                                                                                   | `test_tenders_tas.py`          |
| Login prefix | Upper case; the scraper reads `<PREFIX>_USERNAME` and `<PREFIX>_PASSWORD`.                                                                                   | `TAS`                          |
| Secret names | Lowercase short name for Secret Manager.                                                                                                                     | `tas-username`, `tas-password` |

The folder and `source_id` are not always the same (`buy_nsw` uses `buynsw`; `eTender` uses `qbuild_etender`). Create the folders with `mkdir -p error_scrapers/<folder>/test_cases`. No `__init__.py` anywhere.

## A3. Save the website pages (fixtures)

Fixtures are saved copies of real pages. The tests run against them, so the scraper is checked without touching the live website, and when the site changes later you can see exactly what changed.

In Chrome: open the page, **Ctrl+S** (Cmd+S), choose “Webpage, HTML only”, save into `error_scrapers/<folder>/test_cases/`. If the saved file has no tender rows (filled in by JavaScript): **F12** → Elements → right-click the top `<html>` line → Copy → Copy outerHTML, paste into a text editor, save as `.html`. Do not edit the files by hand; the AI trims and cleans them.

| \#  | Page                                                      | Suggested name                | Needed              |
|-----|-----------------------------------------------------------|-------------------------------|---------------------|
| 1   | The list of open tenders (several showing)                | `<portal>_public_list.html`   | Always              |
| 2   | A tender page that has documents                          | `<portal>_full_details.html`  | Always              |
| 3   | A tender page with no documents                           | `<portal>_no_documents.html`  | If you can find one |
| 4   | The page or form that delivers the documents, if separate | `<portal>_documents.html`     | If it exists        |
| 5   | The login page, signed out                                | `<portal>_login_page.html`    | Login websites      |
| 6   | The page after a wrong password (type one on purpose)     | `<portal>_wrong_pass.html`    | Login websites      |
| 7   | A page you see once signed in (shows Logout or your name) | `<portal>_login_success.html` | Login websites      |
| 8   | A block or challenge page, if you ever see one            | `<portal>_blocked.html`       | Browser websites    |

Also note what a document download delivers: **one zip per tender** or **one file per document**. Pages 5 to 7 may contain your name, email or session codes: the repository is public, and the AI removes these and tells you what it removed.

## A4. Brief the AI

New chat in the repository folder. Paste the standing instructions (Appendix A), then this:

    New scraper request.

    Portal name: [e.g. Tenders Victoria]
    Website: [https://...]
    source_id: [lowercase_with_underscores]
    Folder: error_scrapers/[folder]/
    Test file: test_[anything].py   (must start with test_)
    Site type: [public / login for documents / needs a real browser /
                list loaded by JavaScript / blocks part-way / not sure]
    Login: [none] or [environment variables [PREFIX]_USERNAME and [PREFIX]_PASSWORD;
            the username is an email address / a user ID]
    Documents: [one zip per tender / one file per document / none available / not sure]
    Pages saved in error_scrapers/[folder]/test_cases/: [list each file and what it is]
    Anything unusual: [e.g. the list has several pages; documents need ticking first]

    Please follow the standing instructions. First tell me which existing scraper you
    will copy and what you can and cannot tell from my pages, and ask for anything
    missing. Then write the tests first and show them failing, write the scraper until
    they pass, register it in manager.py, run every check in the standing instructions,
    and report back in plain English.

The AI delivers `error_scrapers/<folder>/scraper.py`, the tests and cleaned fixtures in `test_cases/`, the `manager.py` registration lines, and a `requirements.txt` change only if a new library is needed. Its report must say which files changed, how many tests ran and passed (it must have run them), what the live check showed, what it removed from your pages or guessed, and (login websites) the prefix and secret names for step A7. If your AI cannot write files, ask for each file in full in a code block headed by its path, create them, and carry on.

## A5. Tests and live check

The AI runs these; you can repeat them.

    python -m pytest error_scrapers/<folder>/test_cases -q
    python -m pytest tests error_scrapers -q

Then a live check, which reads three real tenders into `tenders_data/` (login websites need `.env`):

    python -c "from error_scrapers import reporting; reporting.configure_logging(); from error_scrapers.<folder>.scraper import run_scraper; print(run_scraper(limit=3, output_dir='tenders_data'))"

Good looks like: the log starts `START  limit=3` and ends `END    code=0 SITE_SUCCESS`; the last `verdict:` line says every tender was scraped cleanly; `tenders_data/<ref>/` holds `__tender__<ref>.txt` (ending with a `Detail URL:` line), the documents, and a `.txt` beside each readable document; a login website logs that the credentials are set and the login worked. A `TENDER_PARTIAL` is acceptable only if you understand the reasons. Delete `tenders_data/` afterwards. If the website blocks your computer, stop; do not keep retrying.

> For Tenders ACT the entry point is `run_scraper_via_browser` (import it `as run_scraper`); the plain `run_scraper` in that file is an old path the pipeline does not use. Always use the function `manager.py` imports.

## A6. Register in manager.py and test through it

The AI makes four small edits (`git diff manager.py` should show only these):

    # 1. imports
    from error_scrapers.<folder>.scraper import run_scraper as run_<source_id>
    # 2. PORTAL_URL_MAP (the link on the health page)
    "<source_id>": "https://the-portal-home-page",
    # 3. end of SCRAPERS
    ("<source_id>", run_<source_id>),
    # 4. only if the scraper takes known_urls and resume
    CRAWL_RESUME_SOURCES = {"buynsw", "vic_buyingfor", "sa_tenders", "tenders_act", "<source_id>"}

Nothing else needs registering; the health row appears under the `source_id` in capitals. Then run it the way the pipeline does, without waiting for the other ten: put `#` in front of every other line in `SCRAPERS`, save, and run

    python manager.py --local

It takes 2 tenders per uncommented scraper into `tenders_data/`, with no BigQuery, Cloud Storage or health report. The `SCRAPE RUN SUMMARY` table at the end should show your row as `0 SITE_SUCCESS` with 2 tenders (codes: 0 success, 6 partial, 1 unreachable, 2 login failed, 3 blocked, 4 structure changed, 5 rate limited). **Then remove every** `#` **you added** and prove it:

    grep -n '^ *# *("' manager.py

It must print nothing (PowerShell: `Select-String -Path manager.py -Pattern '^ *# *\("'`). This is rule 4: a scraper left commented out is skipped every night. You can ask the AI to do this whole step and show you the check’s output.

## A7. Create the login secrets (login websites only)

Cloud Shell. Set the two names the AI gave you, then look at the existing secrets so yours match:

    PORTAL=sa
    ENVPFX=SA
    gcloud secrets list --project "$PROJECT"

Create the two secrets. **Paste these one at a time**: a pasted `read` swallows the next line you paste. Type the value when prompted (the password does not echo).

    read -rp "Portal username: " PORTAL_USER
    printf '%s' "$PORTAL_USER" | gcloud secrets create "${PORTAL}-username" --project "$PROJECT" --replication-policy=automatic --data-file=-; unset PORTAL_USER
    read -rsp "Portal password: " PORTAL_PASS; echo
    printf '%s' "$PORTAL_PASS" | gcloud secrets create "${PORTAL}-password" --project "$PROJECT" --replication-policy=automatic --data-file=-; unset PORTAL_PASS

Let the job’s service account read them, and attach them to the job:

    SA=$(gcloud run jobs describe "$JOB" --region "$REGION" --project "$PROJECT" --format='value(spec.template.spec.template.spec.serviceAccountName)')
    [ -z "$SA" ] && SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
    echo "$SA"
    gcloud secrets add-iam-policy-binding "${PORTAL}-username" --project "$PROJECT" --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
    gcloud secrets add-iam-policy-binding "${PORTAL}-password" --project "$PROJECT" --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor"
    gcloud run jobs update "$JOB" --region "$REGION" --project "$PROJECT" --update-secrets "${ENVPFX}_USERNAME=${PORTAL}-username:latest,${ENVPFX}_PASSWORD=${PORTAL}-password:latest"
    gcloud run jobs describe "$JOB" --region "$REGION" --project "$PROJECT" --format=yaml | grep "${ENVPFX}_"

The last line should list `${ENVPFX}_USERNAME` and `${ENVPFX}_PASSWORD` (names only, never values). **Always** `--update-secrets`**, never** `--set-secrets`, which would replace every other website’s login. Because the job reads `latest`, a changed secret takes effect next run with no rebuild.

## A8. Commit and merge

Stage files by name; never `git add .`.

    git checkout -b scraper/<folder>
    git add error_scrapers/<folder> manager.py        # and requirements.txt if it changed
    git commit -m "Add <Portal> scraper"
    git push -u origin scraper/<folder>

Open a pull request. “Files changed” should show only the new folder, the small `manager.py` edit and possibly `requirements.txt`; if anything else appears, ask the AI why. Merge into main.

## A9. Build and deploy

The job runs a built **image**, so merging alone changes nothing. A Cloud Build trigger (`Tender-AI-Daily-Build`) rebuilds from main on a schedule, but build by hand to be sure, **between nightly runs** and from a fresh clone of main (the build uploads whatever folder you run it from):

    gcloud run jobs executions list --job "$JOB" --region "$REGION" --project "$PROJECT" --limit 3   # nothing running?
    cd ~ && rm -rf CITS3200_57 && git clone https://github.com/UtkristaUwa/CITS3200_57.git && cd CITS3200_57
    git log --oneline -3                                   # your change on top
    gcloud builds submit --config cloudbuild.yaml --project "$PROJECT" .
    gcloud builds list --project "$PROJECT" --limit 3      # newest row: SUCCESS

If it says `FAILURE`, open the log link the build printed and give the error to the AI (usually a library missing from `requirements.txt`). Use `cloudbuild.yaml` only; `cloudbuild.manager.yaml` refers to a Dockerfile that does not exist.

## A10. Smoke test and check the first run

Start a run now, one tender per website, for this run only:

    gcloud run jobs execute "$JOB" --region "$REGION" --project "$PROJECT" --update-env-vars SCRAPE_LIMIT_CLOUD=1 --wait

It is a real run (it stores documents, calls Gemini, writes BigQuery). The job counts as failed if any one website reports an error, so judge the new scraper from its own health row and logs:

    gcloud storage cat "gs://$BUCKET/scraper_health.json" | python3 -m json.tool

    SITE=<SOURCE_ID_IN_CAPITALS>
    gcloud logging read "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\" AND textPayload:\"$SITE\"" \
      --project "$PROJECT" --freshness=2h --limit=300 --order=desc --format='value(timestamp,textPayload)'

    gcloud storage ls "gs://$BUCKET/tenders/<source_id>/" | head
    bq --project_id="$PROJECT" query --use_legacy_sql=false 'SELECT source_id, COUNT(*) AS tenders, MAX(updated_at) AS last_updated FROM `tenderai-dev.TenderAI.tenders` GROUP BY source_id ORDER BY source_id'

You want: a health row with status `Success` (or `Partial` with reasons you understand; “reason not recorded” is a scraper bug, see Part B); a log with `START`, the login lines, one line per tender like `(1/30) <ref>  SITE_SUCCESS`, `END    code=0` and a clean `verdict:`; documents in the bucket; your `source_id` in the BigQuery count; and the tenders showing on the website. The log command prints newest lines first; `--freshness=2h` keeps it to this run.

# Part B. Keep it healthy, fix it when it breaks

**Weekly check (five minutes).** Open the System Health page: every website should be Success, or Partial with reasons that are not scraper faults (table in B1), and “Last successful run” should be recent. Then in Cloud Shell:

    gcloud run jobs executions list --job "$JOB" --region "$REGION" --project "$PROJECT" --limit 3
    gcloud logging read "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\" AND textPayload:\"Pipeline finished with scraper failures\"" --project "$PROJECT" --freshness=30d --limit=3 --order=desc --format='value(timestamp,textPayload)'

The first shows whether the last runs completed; the second, if a run failed, names the websites and why. One failed website fails the whole job while the others finish normally, so read the line before worrying.

**Fixing.** The loop: **find** which website and what the message says → **collect** the evidence → **brief** the AI → **test** → **deploy** → **check**.

## B1. Find the failing website and read its message

You notice a problem from the System Health page (Partial or Error, or “Last successful run” stops moving), from the job showing as failed in the executions list, or from tenders no longer appearing. If tenders are missing but the website shows Success, the pipeline dropped them after the scrape: search the logs for `Tender processing failed` or `Failed to upsert` (B2) and give those lines to the AI. Read the report:

    gcloud storage cat "gs://$BUCKET/scraper_health.json" | python3 -m json.tool

Each entry has `website`, `url`, `last_run`, `last_successful_run` (moves only on a clean Success), `status` and `message`.

| Health message                                                                | What it means                                                                                                                                                 | What to do                                                                                                                   |
|-------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| Error: “The URL provided could not be reached.”                               | The site is down, moved, or the connection failed. Plain-HTTP scrapers already tried the whole run three times.                                               | Open the site in a browser. Down: wait a run. Moved: give the AI the new address and fresh pages.                            |
| Error: “The site login / portal authentication failed.”                       | Wrong or expired password, or the login form changed.                                                                                                         | Sign in yourself. Password changed: update the secret (B6). Works for you but not the scraper: collect the login pages (B2). |
| Error: “Anti-bot detections … have blocked access.”                           | The site refused the scraper and nothing was kept (VIC and ACT wait and retry before saying this).                                                            | No workarounds. Check the site in a browser, wait a run, raise it with SVA.                                                  |
| Error: “The HTML structure of the website changed; scraper selectors failed.” | Redesign: the listing had no tenders, a tender page had no reference, or (SA, TAS) every tender page failed to parse. For SA also its pager failing part-way. | Collect fresh pages and logs (B2), send the fix request (B3).                                                                |
| Error: “Site rate limited or temporarily blocked access.”                     | HTTP 429, or buy.nsw’s firewall turned us away before anything was read.                                                                                      | Wait a run. If it repeats, ask the AI to lengthen the pause. Never retry harder.                                             |
| Error: “Unhandled exception: …”                                               | The scraper crashed: always a bug. Tenders finished before the crash were kept; the one in progress was stored incomplete.                                    | Collect the lines around `CRASHED after` (the traceback) and send the fix request.                                           |
| Partial: “N of M tenders incomplete: …”                                       | Some tenders are missing something; the reasons are listed.                                                                                                   | See the next table.                                                                                                          |
| Success: “No new tenders to scrape.”                                          | Nothing scraped. Normal for a resuming site with nothing new.                                                                                                 | On any other site, if it lasts, check the logs for the `verdict:` line.                                                      |
| No row, or a very old `last_run`                                              | The job never reached it, or it is not registered.                                                                                                            | Check the executions list and builds (B2) and that it is in `SCRAPERS`.                                                      |

| Partial reason                              | Meaning and action                                                                                                                                                                                                                         |
|---------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| password-protected file dropped             | The site’s own file is locked. Not a scraper fault.                                                                                                                                                                                        |
| text could not be read from a file          | A format the reader cannot open. Ask the AI if it can be supported; otherwise leave it.                                                                                                                                                    |
| empty file on the portal dropped            | The site served an empty file. Not a scraper fault.                                                                                                                                                                                        |
| file over the size limit not stored         | Bigger than `MAX_ATTACHMENT_MB` (Appendix C), whether on its own or inside a zip package (those members are not even unpacked). Not a scraper fault.                                                                                       |
| a document failed to download               | Check the logs for the HTTP error. Retried on later nights; if it keeps failing, a changed link or expired session needs a fix.                                                                                                            |
| documents could not be opened on the portal | Usually the login or the account’s permissions (NT needs a business attached; a QLD tender with no Follow toggle cannot be followed by this account). Check the account in a browser. Retried.                                             |
| file could not be stored                    | Upload to Cloud Storage failed. Check the tender’s log lines and bucket permissions. Retried.                                                                                                                                              |
| error while scraping the tender             | One page broke the scraper, or the run was stopped on this tender. Retried; if it persists, collect that page.                                                                                                                             |
| the tender page could not be read           | (SA, TAS) One odd page while the others parsed: withdrawn or slow to render. (QLD) The tender’s preview page could not be fetched. Nothing is stored for it, so it is tried again next run; if the same tender repeats, collect that page. |
| the list of tenders was not read in full    | A later listing page failed after its retries. Usually clears next night.                                                                                                                                                                  |
| the site stopped answering part-way through | 15 tenders in a row timed out; the rest wait for next night. If it repeats, the site is struggling or blocking us.                                                                                                                         |
| reason not recorded                         | A gap in the scraper: ask the AI to record a reason.                                                                                                                                                                                       |

The report is rewritten after each website finishes, so mid-run some websites are missing: normal. A row can also turn from Success to Partial a few minutes later when the storing stage finds a problem the scrape could not see.

## B2. Collect the evidence

Warnings and errors for one website, newest first (start here); for the whole run drop `AND severity>=WARNING` and raise `--limit`; for an older night raise `--freshness` (checking in the evening, `--freshness=24h` covers that morning’s 5 am run; `--freshness=48h` the one before):

    SITE=<SOURCE_ID_IN_CAPITALS>
    gcloud logging read "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\" AND textPayload:\"$SITE\" AND severity>=WARNING" \
      --project "$PROJECT" --freshness=12h --limit=200 --order=desc --format='value(timestamp,textPayload)'

The end-of-run table, and whether the job and build ran:

    gcloud logging read "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\"" \
      --project "$PROJECT" --freshness=12h --order=asc --format='value(textPayload)' | grep -A 16 "SCRAPE RUN SUMMARY"
    gcloud run jobs executions list --job "$JOB" --region "$REGION" --project "$PROJECT" --limit 5
    gcloud builds list --project "$PROJECT" --limit 5
    gcloud run jobs describe "$JOB" --region "$REGION" --project "$PROJECT" --format=yaml   # image, secret names, settings

Paste only that website’s lines into the AI chat; remove any password, cookie or token first. Then save fresh pages as in A3, under new names (`<portal>_full_details_new.html`): for a layout change, the list page and one tender page; for failing documents, the page or form that delivers them; for a failing login, the login, wrong-password and signed-in pages; for a block, the page it shows (do not try to get past it).

## B3. Brief the AI

New chat in the repository folder. Paste the standing instructions (Appendix A), then:

    Fix request.

    Scraper: [source_id and folder, e.g. nt_qtol / error_scrapers/nt_qtol]
    What the health table says: [status and message]
    What I saw: [e.g. the login works in my browser; the site looks different; started 12 Oct]
    Logs: [the log command's output, only this website's lines]
    New pages saved in error_scrapers/[folder]/test_cases/: [each file and what it is]

    Please follow the standing instructions. First tell me what you think broke and what
    you can and cannot tell from this evidence. Then add a test that reproduces the
    problem and show it failing, fix the scraper, run every check, and report back in
    plain English. Do not change manager.py unless the fix needs it.

The AI keeps the old pages so the old tests still pass (unless the old layout is gone for good, which it will say), works on a branch, and reports. If it cannot write files, ask for a **patch** (a unified diff) and apply it:

    git checkout -b fix/<folder>-<short-description>
    git apply --check fix.patch && git apply fix.patch
    python -m pytest tests error_scrapers/<folder>/test_cases -q
    git add <the files named in the patch>
    git commit -m "Fix <portal>: <what changed>"
    git push -u origin fix/<folder>-<short-description>

If `git apply --check` says the patch does not apply, the code moved on: `git checkout main`, `git pull`, ask for a new patch.

## B4. Test, deploy, check

Repeat the checks from Part A, in this order:

    python -m pytest error_scrapers/<folder>/test_cases -q
    python -m pytest tests error_scrapers -q
    python -c "from error_scrapers import reporting; reporting.configure_logging(); from error_scrapers.<folder>.scraper import run_scraper; print(run_scraper(limit=3, output_dir='tenders_data'))"
    python manager.py --local          # with the other SCRAPERS lines commented out, then restored
    grep -n '^ *# *("' manager.py      # must print nothing

Then, after the AI’s branch is pushed, merge the pull request on GitHub and deploy between nightly runs, exactly as in A9:

    cd ~ && rm -rf CITS3200_57 && git clone https://github.com/UtkristaUwa/CITS3200_57.git && cd CITS3200_57
    git log --oneline -3
    gcloud builds submit --config cloudbuild.yaml --project "$PROJECT" .
    gcloud builds list --project "$PROJECT" --limit 3

Check with the smoke test and the four commands in A10, or wait for the 5 am run. A fixed scraper shows Success (or Partial with only non-scraper reasons) and its `last_successful_run` moves.

## B5. Roll back

On GitHub open the merged pull request, click **Revert**, merge the revert, and rebuild (A9). If a scraper must be stopped at once and cannot be fixed yet, retire it properly (B6) rather than commenting it out (rule 4), and add it back later. Re-running is safe: the job updates tenders rather than duplicating them.

## B6. Special cases

**Password changed.** No code change or rebuild. Set `PORTAL` as in A7, then paste these one at a time:

    read -rsp "New password: " NEWPASS; echo
    printf '%s' "$NEWPASS" | gcloud secrets versions add "${PORTAL}-password" --project "$PROJECT" --data-file=-; unset NEWPASS

Update your local `.env` too. Same with `${PORTAL}-username` if the username changed.

**The site is blocking us.** No workarounds. Wait, slow the scraper down, ask the operator for permission or a data feed, or decide with SVA to stop scraping it.

**buy.nsw stuck on “rate limited” or “No new tenders” night after night.** It reads its listing in instalments and saves where to carry on; if the listing shrinks below the saved page it stalls. Delete the cursor and it starts from page 1 (nothing else is lost):

    gcloud storage rm "gs://$BUCKET/scraper_state/buynsw.json"

**Tuning without code.** Timing settings (Appendix C) change with `gcloud run jobs update "$JOB" --region "$REGION" --project "$PROJECT" --update-env-vars NAME=VALUE`. Always `--update-env-vars`, never `--set-env-vars`. Leave the job’s memory, CPU, timeout and schedule alone.

**Retire a scraper.** Remove its import, `SCRAPERS` line, `PORTAL_URL_MAP` entry and any `CRAWL_RESUME_SOURCES` entry from `manager.py`; merge and rebuild. Stored tenders stay; the health row disappears at the next full run.

# Appendix A. Standing instructions for your AI

Paste all of this at the start of every AI chat, before the request from A4 or B3.

    You are helping maintain TenderAI's web scrapers. You have read and write access to a
    local copy of the repository (https://github.com/UtkristaUwa/CITS3200_57) and can run
    commands. The person you are working with may not be a programmer: explain results in
    plain English. The code is the source of truth: if anything here disagrees with the
    code, follow the code and tell the user what differs.

    WHAT YOU ARE WORKING ON
    - Each scraper lives in error_scrapers/<folder>/scraper.py, with its saved pages
      ("fixtures") and tests in error_scrapers/<folder>/test_cases/. No __init__.py files
      anywhere under error_scrapers/; do not add any.
    - manager.py runs every scraper in its SCRAPERS list once a night (Cloud Run job
      tender-batch-job, 5 am Perth), then stores attachments, runs AI summaries and writes
      to BigQuery. A scraper's only job is to leave one folder per tender in the output
      directory and return a status.
    - Python 3.12. Tests: python -m pytest tests error_scrapers -q (always include tests/).
      Report every failure. Failures in tests/test_tenders_api.py are the API's, not the
      scrapers'; GrantConnect's test_site_login_failed_stops_the_run needs the internet.

    START OF EVERY TASK
    1. Read error_scrapers/common.py, reporting.py and run_notes.py. In manager.py read the
       imports, PORTAL_URL_MAP, SCRAPERS and CRAWL_RESUME_SOURCES.
    2. Choose the closest existing scraper, read it and its test file completely, and say
       which and why. nt_qtol is the best layout reference (login, plain HTTP, zip, re-login).
       tenders_tas: one file per document. tenders_wa, eTender: no login. buy_nsw: public zip,
       resumes. austender, grant_connect: login for the documents page. vic_buyingfor,
       sa_tenders, tenders_act: real browser behind Cloudflare, resume. qld_qtenders: JSON
       endpoint plus a browser for documents.
    3. Look at the user's fixtures before writing code. Say which page elements and
       addresses you will rely on and which parts are guesses. Ask for any page you are
       missing. Do not write the scraper until you have what you need.

    THE CONTRACT
    - Entry point: run_scraper(limit: int = 0, output_dir: str = "tenders_data") ->
      common.ScrapeResult(code, SOURCE_ID, count), decorated with
      @reporting.reported(SOURCE_ID.upper()). limit=0 means every tender. A scraper that can
      stop part-way also takes known_urls=None, resume=False (buy_nsw, vic_buyingfor,
      sa_tenders, tenders_act); vic_buyingfor and sa_tenders also take headless=True.
      tenders_act's pipeline entry point is run_scraper_via_browser.
    - SOURCE_ID is lowercase with underscores; the same string is used in SCRAPERS,
      PORTAL_URL_MAP, BigQuery and the storage path. Never change an existing one.
    - Output: one folder per tender from common.tender_dir(ref, output_dir); ref is the
      site's own reference and must be identical every run. Inside: the attachments;
      __tender__<ref>.txt via common.save_page_text then common.append_full_page_text (no
      visible page text is lost); common.add_source_url(folder, ref, url) with a permanent
      address (strip session, tracking and status parameters). The pipeline matches on
      source+reference or source+URL, so both must be stable. A tender with no reference is
      a structure change; never file several tenders under one folder.
    - Codes (common.py): 0 SITE_SUCCESS, 1 SITE_TOTAL_FAILURE, 2 SITE_LOGIN_FAILED,
      3 SITE_BOT_BLOCKED, 4 SITE_STRUCTURE_CHANGE, 5 SITE_RATE_LIMITED, 6 TENDER_PARTIAL.
      Combine per-tender codes with common.site_code_from(codes). Health page: Success = a
      smooth run (stopping part-way on a site that blocks us is still success); Partial = a
      tender not fully obtained, always with a count and reasons; Error = login failed,
      structure change, rate limited, total failure, blocked, or an unhandled exception.
      manager.py fails the job on 1-5 and on an exception, not on 6.
    - Never let an exception escape run_scraper. Catch httpx.TransportError (not just
      ConnectError) -> SITE_TOTAL_FAILURE; raise_for_status() on every page including the
      login page (a 403/429/5xx there is a block, rate limit or outage, not a structure
      change); catch common.StructureChangedError at run level -> SITE_STRUCTURE_CHANGE;
      wrap any re-login in try/except.

    REQUIRED BEHAVIOURS
    - Logging: log = reporting.site_logger(SOURCE_ID.upper()); per tender
      reporting.tender_line / tender_failed; reporting.documents_line for documents;
      reporting.diagnose once after the loop. No run summary of your own.
    - Reasons: wherever a tender is incomplete, run_notes.note_reason(category, detail) with
      LOCKED, UNREADABLE, EMPTY, DOWNLOAD, PORTAL, SIZE, NOT_STORED, SCRAPE_ERROR or PAGE (a
      tender page that could not be fetched or parsed; LISTING and SITE_STOPPED come from the
      common helpers). A locked file is LOCKED only, never DOWNLOAD as well; an empty download
      is EMPTY, not a document. Name tenders by reference, never URL. If the run can stop
      part-way or resume, call run_notes.note_total. "reason not recorded" is a bug.
    - Blocks: a blocked_code() helper like nt_qtol's: 429 -> SITE_RATE_LIMITED, 403 ->
      SITE_BOT_BLOCKED, stop the run. Never retry a 403, 404, 429 or anything
      common.is_block_response flags. Never evade a block: no rotating addresses, no
      bypassing a firewall or challenge, no captcha solving, no faked fingerprints. Waiting
      and restarting later with a fresh browser is allowed (see vic_buyingfor, tenders_act).
      A challenge page or an empty listing is never "0 tenders, success".
    - Retries: only the common.py helpers: request_with_retries, stream_attachment /
      download_attachment (reject_web_pages stays True), retry_transient, load_page_with_retries,
      wait_for_download. Never repeat a login POST: post it through common.submit_login or
      call common.note_login_attempt() first, so retry_if_site_unreachable does not run the
      scrape again once credentials were sent. On a lapsed session re-login at most
      MAX_RELOGINS (3) times, in try/except, without re-fetching documents already saved.
    - Plain-HTTP scrapers: wrap the run in common.retry_if_site_unreachable; use
      common.DeadSiteWatch in the tender loop; wrap later listing pages in try/except with
      common.listing_page_failed then common.note_listing_problems (-> TENDER_PARTIAL); an
      empty first page is StructureChangedError; stop a listing walk on a repeated page;
      collect unparseable detail pages and call common.level_unparsed_pages after the loop.
    - Browser scrapers: share a state dict between attempts so finished tenders are kept
      and skipped; turn a dead browser into common.BrowserLostError (common.browser_is_gone)
      so the next attempt starts fresh, and re-raise it from a download so that tender is
      redone; a Cloudflare challenge ends the attempt and the wrapper waits and retries; on a
      block or dead browser with a tender folder already open call
      run_notes.note_unfinished(exc) so it is stored incomplete, never as complete with no
      documents; never re-post a login refused on an earlier attempt, and never let an
      earlier attempt's SITE_LOGIN_FAILED outrank a later attempt that succeeded.
    - Credentials: <PREFIX>_USERNAME / <PREFIX>_PASSWORD from the environment
      (load_dotenv() for local .env). Log "set" or "MISSING", never a value. Missing
      credentials with documents behind a login: TENDER_PARTIAL with page text saved. A
      rejected login: SITE_LOGIN_FAILED, page text still scraped where the site allows.
    - Documents: common.unpack_zip; locked files are LOCKED (common.EncryptedFileError);
      text via common.download_attachment / common.extract_attachment_text; use the name
      actually written when a file was renamed "(2)"; skip common.is_junk_file members; a
      local extractor map must cover .pdf .docx .xlsx .xlsm .xls .pptx. Never trim or alter
      what goes to Gemini.
    - Politeness: a short pause between requests, one session, read only. Signing in is
      allowed; never lodge, apply or change anything. If documents need an account action
      (QLD's "follow"), ask the user first.
    - Resume (only sites that block part-way): known_urls and resume, unseen tenders first
      (order_for_resume in vic_buyingfor), and add the source to CRAWL_RESUME_SOURCES.
    - Use a real browser only when a plain request is blocked or the page needs JavaScript.

    TESTS
    - Write them first, copying the reference scraper's test file. Offline only:
      httpx.MockTransport or the reference's fake browser; never the live site, not even
      the login page GET. Cover: listing and detail parse; documents saved; the __tender__
      file and source URL; login success, wrong password, missing credentials; structure
      change (rename a class in the fixture inside the test); pagination and limit; a later
      listing page failing; 429 and 403 not retried; a 5xx and a dropped connection retried
      then succeeding, and exhausted -> partial with a recorded reason; unreachable at start
      -> SITE_TOTAL_FAILURE after the whole-run retry; failed download, locked zip, empty
      file -> the right run_notes category; ScrapeResult code and count for success, partial
      and error; session lapse and re-login if applicable; for browsers: start retry, dead
      browser mid-run, page-load retry, challenge page not retried.
    - Autouse fixture: run_notes.reset(), pause constants 0, credential env vars cleared
      (see test_tenders_wa.py). Any test that makes a plain-HTTP scraper fail completely must
      monkeypatch common.RUN_RETRY_WAITS = () or it waits four minutes.
    - Show the tests failing for the right reason first, then passing. Never claim a test
      passed unless you ran it and saw it pass.

    FIXTURES
    - Real saved pages in test_cases/, trimmed: remove script, style, svg; cut lists to about
      3 entries; keep real structure and class names. The repository is public: remove names,
      emails, phone numbers, account IDs, cookies, tokens, session-specific hidden values
      (search "@", "token", "session", "csrf" and the user's name and organisation) and tell
      the user what you removed. Never open, print, copy or commit .env. Never ask for a
      password or cookie.

    REGISTRATION (new scrapers): the import, a PORTAL_URL_MAP entry, a SCRAPERS entry, and a
    CRAWL_RESUME_SOURCES entry only if it resumes. No other change to manager.py. Add to
    requirements.txt only what is missing. Nothing else needs registering.

    CHECKS BEFORE YOU HAND BACK
    1. python -m pytest error_scrapers/<folder>/test_cases -q
    2. python -m pytest tests error_scrapers -q, and report every failure
    3. Capped live run: python -c "from error_scrapers import reporting;
       reporting.configure_logging(); from error_scrapers.<folder>.scraper import run_scraper;
       print(run_scraper(limit=3, output_dir='tenders_data'))" (tenders_act: import
       run_scraper_via_browser as run_scraper; qld_qtenders: credentials in the shell, .env
       is not loaded). Check tenders_data/<ref>/ and that the log ends END code=0 with a
       sensible verdict line.
    4. Through manager.py: comment out every other SCRAPERS line, python manager.py --local,
       read SCRAPE RUN SUMMARY, remove every # you added, then grep -n '^ *# *("' manager.py
       (Windows: Select-String -Path manager.py -Pattern '^ *# *\("') must print nothing.
    5. Delete tenders_data/.
    If you cannot reach the website from where you run, ask the user to run the live checks
    and paste the output.

    GIT AND SCOPE
    - Branch scraper/<folder> (new) or fix/<folder>-<short-description> (fix). Stage files
      by name; never git add .; never stage tenders_data/, .env or logs. Do not merge,
      deploy or run gcloud; the user does those. If you cannot write files, give each file in
      full in a code block headed by its path, or a unified diff for a fix.
    - Change nothing outside error_scrapers/<folder>, the registration lines in manager.py
      and requirements.txt. If common.py, reporting.py or run_notes.py must change, stop,
      explain why and wait for approval. Never edit api/, frontend/, processing/,
      ingestion/, attachment_store.py, the Dockerfile or cloudbuild.yaml.

    WHEN YOU HAND BACK: the files created or changed; test counts as you ran them; what the
    live check showed; anything guessed, removed from fixtures or unverified; what the user
    must do next (secrets, merge, deploy) by guide step number; for a login site, the login
    prefix and secret short name.

# Appendix B. The existing scrapers

| source_id        | Folder          | Login prefix    | How it fetches                                | Documents                                                                       | Resumes                   |
|------------------|-----------------|-----------------|-----------------------------------------------|---------------------------------------------------------------------------------|---------------------------|
| `grantconnect`   | `grant_connect` | `GRANTCONNECT_` | Plain HTTP                                    | Login; documents page                                                           | No                        |
| `buynsw`         | `buy_nsw`       | none            | Plain HTTP                                    | Public zip                                                                      | From a saved listing page |
| `tenders_act`    | `tenders_act`   | `ACT_`          | Real browser (`run_scraper_via_browser`)      | Login; zip from a download form                                                 | Yes                       |
| `austender`      | `austender`     | `AUSTENDER_`    | Plain HTTP                                    | Login for the documents page                                                    | No                        |
| `nt_qtol`        | `nt_qtol`       | `NT_QTOL_`      | Plain HTTP                                    | Login (a User ID, not an email); one zip; re-login on lapse. **Best reference** | No                        |
| `qld_qtenders`   | `qld_qtenders`  | `QLD_`          | JSON endpoint + real browser for documents    | Login (an email); tender must be followed first                                 | No (retry rounds)         |
| `qbuild_etender` | `eTender`       | none            | Plain HTTP                                    | None offered; page text only                                                    | No                        |
| `vic_buyingfor`  | `vic_buyingfor` | `VIC_`          | Real browser (Cloudflare)                     | Login; zip                                                                      | Yes                       |
| `tenders_wa`     | `tenders_wa`    | none            | Plain HTTP                                    | Anonymous “Download for Information Only” zip                                   | No                        |
| `sa_tenders`     | `sa_tenders`    | `SA_`           | Real browser (Cloudflare); list by JavaScript | Login; zip                                                                      | Yes                       |
| `tenders_tas`    | `tenders_tas`   | `TAS_`          | Plain HTTP                                    | Login; one file per document                                                    | No                        |

The older `web_scrapers/` folder is not a reference for anything new.

# Appendix C. Settings and where things live

Job settings you may change with `--update-env-vars` (defaults are the code’s values; `gcloud run jobs describe` shows what is actually set):

| Variable                                                                            | Default        | What it does                                                                                                             |
|-------------------------------------------------------------------------------------|----------------|--------------------------------------------------------------------------------------------------------------------------|
| `SCRAPE_LIMIT_CLOUD`                                                                | 0 (all)        | Tenders per website. Set on one execution for a smoke test.                                                              |
| `PIPELINE_WORKERS`                                                                  | 4              | Tenders stored and processed at once.                                                                                    |
| `MAX_ATTACHMENT_MB`                                                                 | 500            | Largest attachment stored; larger members inside a zip package are not unpacked either.                                  |
| `VIC_` / `SA_` / `ACT_DOWNLOAD_WAIT_SECONDS`, `_STALL_SECONDS`, `_HARD_MAX_SECONDS` | 480, 480, 1800 | Browser download: time to start, time it may sit still, hard maximum.                                                    |
| `TAS_PAGE_TIMEOUT_SECONDS`, `TAS_FILE_TIMEOUT_SECONDS`                              | 120, 180       | TAS’s timeouts.                                                                                                          |
| `QLD_*`                                                                             | see code       | QLD has its own download, retry and browser settings (top of `error_scrapers/qld_qtenders/scraper.py`). Developers only. |

Fixed in code (change with a fix): whole-run retry waits 60 s and 180 s (`common.RUN_RETRY_WAITS`), the 15-tender dead-site cut-off (`common.DEAD_SITE_AFTER`), ACT’s Cloudflare wait (120 s × attempt). Leave alone: `PIPELINE_MODE` / `REPROCESS_LIMIT` (the admin Config page’s reprocess button sets these on one-off executions), `ATTACHMENTS_BUCKET`, `ATTACHMENT_UPLOADS`, `KEEP_LOCAL_COPIES`, `RUNTIME_CONFIG_*`, `PIPELINE_DEBUG`, `RUNNING_IN_CONTAINER`.

| Where                                                        | What                                                                                       |
|--------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| `error_scrapers/<folder>/scraper.py`, `test_cases/`          | One scraper, its fixtures and tests.                                                       |
| `error_scrapers/common.py`, `reporting.py`, `run_notes.py`   | Shared helpers and codes; log wording; health-page reasons. Every scraper depends on them. |
| `tests/`                                                     | Cross-scraper, pipeline and API tests.                                                     |
| `manager.py`                                                 | The nightly run; `SCRAPERS` and the registration.                                          |
| `Dockerfile`, `cloudbuild.yaml`                              | The image the job runs (Python 3.12 + Chrome).                                             |
| `tenders_data/`, `.env`                                      | Local output and local logins. Never committed.                                            |
| `gs://tenderai-dev-documents/scraper_health.json`            | The health report.                                                                         |
| `gs://tenderai-dev-documents/scraper_state/<source_id>.json` | Where a resuming crawl (buy.nsw) left off.                                                 |
| `gs://tenderai-dev-documents/tenders/<source_id>/<ref>/`     | Stored documents.                                                                          |
| BigQuery `tenderai-dev.TenderAI.tenders`                     | The tender database.                                                                       |

# Appendix D. Quick troubleshooting

| Problem                                                        | Do this                                                                                                                       |
|----------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| `pip install` fails building PyMuPDF                           | You are on Python 3.13; use 3.12.                                                                                             |
| pytest stops at once with “errors during collection”           | Install all three requirements files (2.2).                                                                                   |
| pytest runs nothing or the wrong tests                         | Name both folders: `python -m pytest tests error_scrapers -q`.                                                                |
| A test takes minutes or hangs                                  | It makes a scraper fail completely; monkeypatch `common.RUN_RETRY_WAITS = ()` in the test.                                    |
| `manager.py --local` says “default credentials were not found” | `gcloud auth application-default login` (2.4).                                                                                |
| Logs say the login is MISSING                                  | Secret not attached or names differ: step A7’s last command, compare with what the scraper reads.                             |
| `PERMISSION_DENIED` on a secret or job command                 | Use the project Owner, or get Secret Manager and Cloud Run admin roles.                                                       |
| The job failed but only one website shows Error                | Any error code on any website fails the job; read the failure line (`textPayload:"Pipeline finished with scraper failures"`). |
| Websites vanished from the health page after a test            | A cloud run was made with scrapers commented out: restore the list, rebuild, start a full run without `SCRAPE_LIMIT_CLOUD`.   |
| A browser scraper will not start locally                       | Install Google Chrome.                                                                                                        |
| The AI asks for a password or cookie                           | Refuse. Passwords go only into Cloud Shell prompts and `.env`.                                                                |
