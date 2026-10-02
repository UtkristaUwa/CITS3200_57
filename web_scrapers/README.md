# Web scrapers

> **Superseded for the daily pipeline.** `manager.py` now runs AusTender, NT
> QTOL, QLD QTenders and Buying for Victoria from `error_scrapers/` (see
> [error_scrapers/README.md](../error_scrapers/README.md)), which write the
> `__tender__<REF>.txt` + attachments layout and return status codes. The
> modules below are kept for reference until they are removed.

## Output format

Every scraper writes **one directory per tender** — never one file per website:

```
tenders_data/
  ATM_2026_3494/
    ATM_2026_3494.txt      text scraped from the tender's own page
    tender.json            the tender in ingestion's 20-field shape
    Attachment A.pdf       the documents that were actually downloadable
    Addendum 1.pdf
```

The `.txt` file is always named after its directory, which is what
`document_scraper/main.py` looks for when it appends extracted attachment text.

`tender.json` matches [`ingestion/sample_tender.json`](../ingestion/sample_tender.json)
and validates against [`ingestion/tender.schema.json`](../ingestion/tender.schema.json),
so a scrape feeds straight into `ingestion/validate_and_submit.py` with no
reshaping. That schema sets `"additionalProperties": false`, so everything the
scraper knows that it does not model — which documents were downloadable, where
each file landed, whether the portal demanded a login — lives under
`raw_extra.scrape`:

```json
{
  "source_id": "austender",
  "source_reference_id": "ATM_2026_3494",
  "source_url": "https://www.tenders.gov.au/Atm/Show/...",
  "title": "Geological Disposal Programme Options",
  "issuing_agency": "Department of Industry, Science and Resources",
  "category": "tender",
  "status": "open",
  "publish_date": "2026-08-13",
  "closing_date": "2026-09-10",
  "documents": [
    { "file_name": "Attachment A.pdf", "file_type": "pdf", "extracted_text": null }
  ],
  "raw_extra": {
    "scrape": {
      "documents_advertised": 4,
      "documents_downloaded": 4,
      "documents_require_login": false,
      "documents_detail": [{ "local_path": "Attachment A.pdf", "bytes_written": 1381228 }]
    }
  }
}
```

`extracted_text` is left null — pulling text out of the files is the
document-extraction stage's job, and it fills that in before submission.
`raw_extra.scrape` is also how a consumer tells an empty `documents` array
meaning "this tender has no attachments" from one meaning "the portal would not
give them to us".

`web_scrapers/common.py` owns this layout. Scrapers call `tender_dir()`,
`write_tender_text()`, `download_document()` and `write_tender_record()` rather than
building paths themselves, so the format stays identical across sources.

## Sources

| Source | Module | Browser? | Documents |
|---|---|---|---|
| AusTender (federal) | `austender/austender.py` | no | downloadable for most tenders; some need a registered-user login |
| Tenders WA | `wa_tenders/wa_tenders.py` | no | names, versions and types are public; the files come as one zip behind a login |
| QTOL (NT) | `nt_qtol/nt_qtol.py` | no | no filenames published at all; the profile needs an account with a business attached |
| Buying for Victoria | `vic_buyingfor/vic_buyingfor.py` | **yes** | names/versions/sizes are public, files need a login |
| QTenders (QLD) | `qld_qtenders/qld_qtenders.py` | **yes** | count only — filenames and files need a VendorPanel supplier account |

Where a portal will not hand over its files, the manifest records the documents
it advertises with `documents_require_login: true`, so the gap is visible
rather than silently looking like a tender with no attachments. QTOL is the
case that makes this necessary: it names no documents at all, so an empty
`documents` array is the *only* thing it ever produces, and `requires_login` is
what distinguishes "nothing attached" from "we were not allowed to look".

Two of the five drive a real Chrome, because those portals render their results
client-side and sit behind Cloudflare. The other three are plain HTTP and run
anywhere. See [INTEGRATION.md](INTEGRATION.md) for what that means for the
pipeline image.

### Credentials

None are required to run: every source scrapes its tender text anonymously.
They only affect whether documents come down.

| Variable | Source | Notes |
|---|---|---|
| `AUSTENDER_USERNAME` / `AUSTENDER_PASSWORD` | AusTender | a registered-user session |
| `WA_TENDERS_COOKIE` | Tenders WA | `JSESSIONID=...` from a signed-in browser. Preferred: the portal emails a verification token at login, which nothing here can answer |
| `WA_TENDERS_USERNAME` / `WA_TENDERS_PASSWORD` | Tenders WA | used when no cookie is set; works only for accounts the portal does not challenge for a token |
| `NT_QTOL_COOKIE` | QTOL | `.AspNet.ApplicationCookie=...` from a signed-in browser |

Never commit any of these. In deployment they belong in Secret Manager.

## Running locally

```bash
pip install -r web_scrapers/requirements.txt

python -m web_scrapers.austender.austender --limit 5
python -m web_scrapers.wa_tenders.wa_tenders --limit 5
python -m web_scrapers.nt_qtol.nt_qtol --limit 5
LIMIT=5 python -m web_scrapers.vic_buyingfor.vic_buyingfor
LIMIT=5 python -m web_scrapers.qld_qtenders.qld_qtenders

# several sources through the shared entrypoint
python -m web_scrapers.run_scrapers --sources austender,wa,nt --limit 5 --output-dir ./out
```

The VIC and QLD scrapers drive a real Chrome (SeleniumBase UC mode) because
those portals render their results client-side and sit behind Cloudflare.
AusTender, WA and NT are plain HTTP. With no Chrome installed the two browser
sources are reported as skipped rather than raising a driver error.

## How the pipeline calls this

The scrapers are a library. `manager.py` imports them and runs them in-process
as the first stage of the daily pipeline:

```python
from web_scrapers import run_scraper

result = run_scraper(limit=10, output_dir=temp_dir)
for folder in result.tender_dirs:
    ...
```

Nothing here deploys or schedules itself, and nothing here uploads: the folders
are the output and the manager decides what happens to them. The full contract,
what the pipeline image needs, and the open decisions are in
[INTEGRATION.md](INTEGRATION.md).

## Tests

```bash
pytest tests                    # offline: fixtures + a fake portal on localhost
pytest tests --live             # also hits the real portals (slow, needs network)
```

Offline tests are deterministic and need no network or credentials. The live
tests answer a different question — is the portal still up and still shaped the
way we expect — and are the natural hook for the scraper error-detection and
alerting work.
