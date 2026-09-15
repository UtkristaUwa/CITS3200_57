# How the pipeline calls the scrapers

The scraper stage is a **library the manager function imports**, not a service
and not a job. `manager.py` is the only thing that is scheduled; it decides
when the scrape runs, and what becomes of the folders afterwards.

This replaces the previous arrangement, where `web_scrapers` deployed itself as
a Cloud Run job with its own 5am Cloud Scheduler trigger. That job and that
schedule are redundant now — [teardown](#tearing-down-the-old-job) below.

## The contract

```python
from web_scrapers import run_scraper

result = run_scraper(limit=10, output_dir=temp_dir)

for folder in result.tender_dirs:
    ...   # document extraction, then process_tender(folder)
```

| | |
|---|---|
| **Call** | `run_scraper(limit=10, output_dir=None, sources=None)` |
| `limit` | max tenders **per source**; `0` means no cap |
| `output_dir` | where to write. The manager passes its temp dir |
| `sources` | list or comma-separated string; defaults to `$SOURCES`, then `austender` |
| **Returns** | a `ScrapeResult` |
| **Raises** | only on caller error (an unknown source name). A portal being down is reported, not raised |

`ScrapeResult`:

| Field | Meaning |
|---|---|
| `tender_dirs` | every directory written, each holding a `tender.json`. **This is the handoff** |
| `counts` | `{source: tenders written}` |
| `failed` | `{source: "ExcType: message"}` — it ran and blew up |
| `skipped` | `{source: reason}` — it never ran (see [Chrome](#the-pipeline-image-needs-chrome)) |
| `total`, `ok`, `summary()` | convenience |

`failed` and `skipped` are separate on purpose. Both leave zero folders behind,
and only one of them means the portal has changed shape — the other means our
image is wrong. Log `result.summary()` and the difference is visible without
reading a stack trace.

Nothing is uploaded and nothing is deleted. The folders in `output_dir` are the
entire output.

## What lands on disk

One directory per tender — never one file per website:

```
<output_dir>/
  ATM_2026_3494/
    ATM_2026_3494.txt      text scraped from the tender's own page
    tender.json            the tender in ingestion's 20-field shape
    Attachment A.pdf       the documents that were actually downloadable
    Addendum 1.docx
```

The `.txt` is always named after its directory, which is what
`document_scraper/main.py` looks for. `tender.json` validates against
`ingestion/tender.schema.json`, and its `raw_extra.scrape` block records what
the schema does not model: how many documents the page advertised, how many
came down, and whether the portal demanded a login. That is how a consumer
tells "this tender has no attachments" from "the portal would not give them to
us" — both are an empty `documents` array otherwise.

## Changes the manager needs

**1. One import line.** `manager.py` currently has:

```python
from web_scrapers.webscraperinit import run_scraper as run_web_scraper
```

`webscraperinit.py` is the pre-rewrite single-file AusTender scraper. It writes
the old layout and it is the only one of the sources it covers. Replace with:

```python
from web_scrapers import run_scraper as run_web_scraper
```

The call itself — `run_web_scraper(limit=10, output_dir=temp_dir)` — is
unchanged, and so is iterating the folders afterwards. Deleting
`webscraperinit.py` at the same time avoids two AusTender scrapers drifting
apart.

**2. Use the result instead of `os.listdir`.** `result.tender_dirs` skips the
stray files a portal occasionally drops in the working directory, and gives you
`failed` / `skipped` for the run log.

**3. Requirements.** The pipeline `requirements.txt` needs
`httpx`, `beautifulsoup4` and `seleniumbase` (see
`web_scrapers/requirements.txt`). `google-cloud-storage` is only needed if the
pipeline chooses to use `web_scrapers/storage.py` for the upload.

### The pipeline image needs Chrome

VIC and QLD render their search results client-side and sit behind Cloudflare,
so they drive a real Chrome. SeleniumBase fetches a matching chromedriver on
demand but **cannot install the browser** — on a plain `python:3.11-slim` image
there is no Chrome at all, and those two sources cannot run.

`run_scraper` checks for a browser before launching one, so today the pipeline
image would give you:

```
0 tender(s); from austender=8; skipped: qld, vic
```

rather than a Selenium exception that reads like a Cloudflare block. To get all
three sources, copy the marked Chrome layer out of `web_scrapers/Dockerfile`
into the repo-root Dockerfile. Two consequences worth knowing before you do:

- **Memory.** A browser run needs ~4Gi. The AusTender-only path needs a
  fraction of that.
- **Headed, not headless.** Chrome runs against an Xvfb virtual display because
  Cloudflare challenges headless Chrome far harder. `run_scraper` starts and
  stops that display itself when `RUNNING_IN_CONTAINER=1` and a browser source
  is requested; the manager does not have to do anything.

If you would rather keep the pipeline image small, the alternative is to leave
the browser sources out of the daily run and scrape them separately — but that
is the arrangement we just removed, so it needs a decision rather than a
default.

## Credentials

AusTender attachment downloads use a registered-user session when one is
available: `AUSTENDER_USERNAME` / `AUSTENDER_PASSWORD`, read from the
environment, Secret Manager in deployment. Without them the scrape still runs
and still writes one folder per tender; the attachments are recorded as
`documents_require_login: true`.

Note that `web_scrapers/webscraperinit.py` on `host_on_gcp` has a username and
password written into the source. They are in git history, so rotating the
account is the fix, not just deleting the lines.

## Tearing down the old job

The Cloud Run job and its schedule still exist in `tenderai-dev` and will keep
scraping at 5am until they are removed, which would double up on whatever the
manager does:

```bash
gcloud scheduler jobs delete tender-scrapers-daily \
  --location australia-southeast1 --project tenderai-dev
gcloud run jobs delete tender-scrapers \
  --region australia-southeast1 --project tenderai-dev
```

The image in Artifact Registry can stay or go; it costs a few cents a month and
nothing points at it.

## Open decisions

1. **Chrome in the pipeline image, or browser sources dropped from the daily
   run?** See above. Without a decision the daily run is AusTender only.
2. **Who uploads the attachments?** Ramon confirmed on 3 Sep that we host
   documents ourselves rather than linking out. The scraper does not upload;
   `web_scrapers/storage.py` has a working `publish()` that mirrors folders to
   `gs://<bucket>/raw/<source_id>/<REF>/` if the pipeline wants to reuse it.
   Raw scrape output is namespaced under `raw/` so it cannot collide with
   whatever prefix the API builds signed document URLs from.
3. **Should `tender.json` seed the AI stage?** `process_tender()` currently
   reads only the `.txt` files and asks the model for `source_id`,
   `source_reference_id`, `title`, `closing_date` and the rest. The scraper
   already has those exactly, from the page's own fields, in the schema's
   shape. Handing them over as knowns would cut tokens and remove a class of
   extraction error — the model would be summarising and tagging rather than
   re-deriving what we already parsed.

## Running it yourself

```bash
pip install -r web_scrapers/requirements.txt

python -m web_scrapers.run_scrapers --sources austender --limit 5
python -m web_scrapers.run_scrapers --sources austender,vic,qld --limit 5 --output-dir ./out

pytest tests            # offline: fixtures and a fake portal on localhost
pytest tests --live     # also hits the real portals
```
