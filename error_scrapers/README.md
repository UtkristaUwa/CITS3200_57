# Scrapers

Every scraper `manager.py` runs lives here, one directory per portal:
`<portal>/scraper.py` plus `<portal>/test_cases/` (captured HTML and the tests
that run against it).

| Portal | Directory | Browser? | Documents |
|---|---|---|---|
| AusTender | `austender/` | no | need `AUSTENDER_USERNAME` / `AUSTENDER_PASSWORD` |
| GrantConnect | `grant_connect/` | no | need `GRANTCONNECT_USERNAME` / `GRANTCONNECT_PASSWORD` |
| buy.nsw | `buy_nsw/` | no | public |
| Tenders ACT | `tenders_act/` | yes | need `ACT_USERNAME` / `ACT_PASSWORD` |
| NT QTOL | `nt_qtol/` | no | need `NT_QTOL_COOKIE` (a signed-in session cookie; the account needs a business attached) |
| QLD QTenders | `qld_qtenders/` | no | need a VendorPanel supplier login, which nothing here supports yet |
| Buying for Victoria | `vic_buyingfor/` | yes (Cloudflare) | need `VIC_USERNAME` / `VIC_PASSWORD` |

## The output standard

`run_scraper(limit=0, output_dir="tenders_data")` returns
`(status_code, tenders)`. `limit=0` means every tender.

Each tender gets its own folder, named after its reference:

```
<output_dir>/<REF>/
    __tender__<REF>.txt     the tender's own page text
    <attachment files>      the original downloads
    <attachment>.txt        text extracted from each PDF/DOCX/XLSX
```

and one entry in `tenders`:

```python
{"tender_id": REF, "title": ..., "folder": ..., "source_url": ...,
 "attachments": [{"file_name", "content_type", "size_bytes"}, ...]}
```

`attachments` is what `attachment_store` uploads, so it lists the real
downloads and never the generated `.txt` files.

Status codes (`common.py`):

| Code | Meaning |
|---|---|
| `SITE_SUCCESS` (0) | everything worked |
| `SITE_TOTAL_FAILURE` (1) | the portal couldn't be reached |
| `SITE_LOGIN_FAILED` (2) | credentials were set but the login didn't take |
| `SITE_BOT_BLOCKED` (3) | anti-bot protection blocked us |
| `SITE_STRUCTURE_CHANGE` (4) | a page no longer looks the way the parser expects |
| `SITE_RATE_LIMITED` (5) | HTTP 429 |
| `TENDER_PARTIAL` (6) | some tenders are missing something, most often documents behind a login we don't have |

The page text is still written when documents are behind a login, so a tender
is never dropped just because its attachments couldn't be fetched.

## Running one

```bash
python -m error_scrapers.austender.scraper --limit 5 --output-dir ./out
python -m error_scrapers.nt_qtol.scraper --limit 5 --output-dir ./out
python -m error_scrapers.qld_qtenders.scraper --limit 5 --output-dir ./out
python -m error_scrapers.vic_buyingfor.scraper --limit 5 --output-dir ./out
```

## Tests

```bash
pytest tests/conftest.py error_scrapers
```

Offline: network is faked with `httpx.MockTransport` (or a fake browser
session for VIC), so no credentials, network or Chrome are needed.
