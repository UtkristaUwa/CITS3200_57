# Web scrapers

## Output format

Every scraper writes **one directory per tender** — never one file per website:

```
tenders_data/
  ATM_2026_3494/
    ATM_2026_3494.txt      text scraped from the tender's own page
    documents.json         manifest of every document the page advertises
    Attachment A.pdf       the documents that were actually downloadable
    Addendum 1.pdf
```

The `.txt` file is always named after its directory, which is what
`document_scraper/main.py` looks for when it appends extracted attachment text.

`documents.json` is written even when nothing could be downloaded, so a
consumer can tell "this tender has no attachments" from "the attachments are
behind a login we do not have":

```json
{
  "reference": "ATM_2026_3494",
  "source_id": "austender",
  "source_url": "https://www.tenders.gov.au/Atm/Show/...",
  "scraped_at": "2026-09-06T03:14:39+00:00",
  "documents_advertised": 4,
  "documents_downloaded": 4,
  "documents_require_login": false,
  "documents": [{ "file_name": "...", "url": "...", "downloaded": true,
                  "local_path": "...", "bytes_written": 1381228, "error": null }]
}
```

`web_scrapers/common.py` owns this layout. Scrapers call `tender_dir()`,
`write_tender_text()`, `download_document()` and `write_manifest()` rather than
building paths themselves, so the format stays identical across sources.

## Sources

| Source | Module | Documents |
|---|---|---|
| AusTender (federal) | `austender/austender.py` | downloadable for most tenders; some need a registered-user login |
| Buying for Victoria | `vic_buyingfor/vic_buyingfor.py` | names/versions/sizes are public, files need a login |
| QTenders (QLD) | `qld_qtenders/qld_qtenders.py` | count only — filenames and files need a VendorPanel supplier account |

Where a portal will not hand over its files, the manifest records the documents
it advertises with `documents_require_login: true`, so the gap is visible
rather than silently looking like a tender with no attachments.

### Credentials

AusTender attachment downloads use a registered-user session when one is
available. Set `AUSTENDER_USERNAME` / `AUSTENDER_PASSWORD` in the environment
(Secret Manager in Cloud Run). Never commit them. Without them the scraper
still runs and still writes one folder per tender.

## Running locally

```bash
pip install -r web_scrapers/requirements.txt

python -m web_scrapers.austender.austender --limit 5
LIMIT=5 python -m web_scrapers.vic_buyingfor.vic_buyingfor
LIMIT=5 python -m web_scrapers.qld_qtenders.qld_qtenders

# all sources through the job entrypoint
python -m web_scrapers.run_scrapers --sources austender,vic --limit 5
```

The VIC and QLD scrapers drive a real Chrome (SeleniumBase UC mode) because
those portals render their results client-side and sit behind Cloudflare.
AusTender is plain HTTP.

## Tests

```bash
pytest                    # offline: fixtures + a fake portal on localhost
pytest --live             # also hits the real portals (slow, needs network)
```

Offline tests are deterministic and need no network or credentials. The live
tests answer a different question — is the portal still up and still shaped the
way we expect — and are the natural hook for the scraper error-detection and
alerting work.

## Cloud Run

Built and deployed as a Cloud Run **job** (batch, not a service):

```bash
gcloud builds submit --tag australia-southeast1-docker.pkg.dev/tenderai-dev/tender-repo/tender-scrapers:latest \
  --project tenderai-dev
gcloud run jobs execute tender-scrapers --region australia-southeast1 --project tenderai-dev
```

The image ships Google Chrome and Xvfb, so all three sources run in the cloud,
not just AusTender. Chrome runs *headed* against a virtual display rather than
truly headless, because Cloudflare challenges headless Chrome much harder; the
container also passes `--no-sandbox` (Chrome will not run as root without it)
and moves shared memory off the container's 64MB `/dev/shm`. All of that is
driven by `RUNNING_IN_CONTAINER` and `DISPLAY`, so local runs are unaffected.

Browser runs need more memory than the AusTender-only path: give the job at
least 4Gi if `SOURCES` includes `vic` or `qld`. All three sources are verified
working in Cloud Run, Cloudflare included — a GCP egress IP is not blocked by
Buying for Victoria today, but that is the portals' call and could change, which
is what the live tests are for.

The local directory is the primary output. Mirroring to Cloud Storage is
additive and off unless `OUTPUT_BUCKET` is set; when set, each tender folder
goes to `gs://<bucket>/raw/<source_id>/<REF>/`, namespaced away from any prefix
the front end serves document URLs from. The bucket stays private — serving
files to the UI is the API's job, via signed URLs.

| Variable | Default | Meaning |
|---|---|---|
| `SOURCES` | `austender` | comma-separated: `austender,vic,qld` |
| `LIMIT` | `10` | max tenders per source (0 = no limit) |
| `MAX_PAGES` | `0` | qld only: search pages to walk (0 = all). `LIMIT` trims only *after* pagination, so this is what shortens a QLD run. |
| `OUTPUT_DIR` | temp dir | where to write |
| `OUTPUT_BUCKET` | unset | mirror results to this bucket |
| `OUTPUT_PREFIX` | `raw` | prefix within that bucket |
