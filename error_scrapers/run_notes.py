"""
What happened to each tender during one scraper's run, and the wording the
System Health table shows for it.

Scrapers report a tender's code through reporting.tender_line(); the places
that find a problem (a file that could not be read, a document that would not
download) add a reason with note_reason() first. Nothing here needs a scraper to
change its return value. manager.py reads snapshot() after each scraper and
describe() turns it into the status chip and message.

The model:
  * Success  - the run went smoothly. That includes buy.nsw and VIC stopping
               part-way because of their WAF / Cloudflare: that is the intended
               flow, and the next run carries on by itself.
  * Partial  - at least one tender is incomplete, whoever's fault it is. Always
               reported, with a count and the reasons.
  * Error    - the run itself failed (login, structure change, rate limit...).

No third-party imports, so attachment_store.py can use it too.
"""

import json
import re
import threading
from collections import Counter
from urllib.parse import urlsplit

SITE_SUCCESS = 0
TENDER_PARTIAL = 6
# Codes where the run itself failed. Same set as manager.FAILURE_CODES.
FAILURE_CODES = {1, 2, 3, 4, 5}

# Reason categories. Short, plain, and each says what is wrong.
LOCKED = "password-protected file dropped"
UNREADABLE = "text could not be read from a file"
EMPTY = "empty file on the portal dropped"
DOWNLOAD = "a document failed to download"
PORTAL = "documents could not be opened on the portal"
SIZE = "file over the size limit not stored"
NOT_STORED = "file could not be stored"
SCRAPE_ERROR = "error while scraping the tender"
PAGE = "the tender page could not be read"
LISTING = "the list of tenders was not read in full"
SITE_STOPPED = "the site stopped answering part-way through"
UNKNOWN = "reason not recorded"

_lock = threading.RLock()
_partials: dict[str, list[tuple[str, str | None]]] = {}
_pending: list[tuple[str, str | None]] = []
_total: int | None = None
_current: str | None = None  # the tender being scraped right now, once its ID is known


def _short_url(url: str) -> str:
    """A page address cut down to the part that tells tenders apart."""
    parts = urlsplit(url)
    if parts.query:
        return parts.query.split("=")[-1].split("&")[-1][:40]
    segments = [segment for segment in parts.path.split("/") if segment]
    return (segments[-1] if segments else parts.netloc)[:40]


def _key(ref) -> str:
    """
    Same folder-safe form the pipeline uses, so stage 1 and stage 2 agree. A
    page address (a scraper that never learned the tender's ID) is cut down to
    its last part so the message stays readable.
    """
    text = str(ref)
    if text.lower().startswith(("http://", "https://")):
        text = _short_url(text)
    name = text.replace("/", "_").replace("\\", "_")
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip() or "unknown"


def reset() -> None:
    """Start of a scraper's run."""
    global _total, _current
    with _lock:
        _partials.clear()
        _pending.clear()
        _total = None
        _current = None


def note_current(ref) -> None:
    """
    The scraper has just learned the ID of the tender it is working on
    (common.tender_dir calls this). If the tender then raises, the error is
    filed under this ID instead of the page address.
    """
    global _current
    with _lock:
        _current = _key(ref)


def note_reason(category: str, detail: str | None = None) -> None:
    """Something went wrong with the tender being scraped right now."""
    with _lock:
        if (category, detail) not in _pending:
            _pending.append((category, detail))


def note_total(total: int) -> None:
    """How many tenders the run set out to do (for "12 of 30")."""
    global _total
    with _lock:
        _total = total


def note_tender(ref, code: int) -> None:
    """
    A tender finished with `code`. Partial tenders keep the reasons noted since
    the last one; anything else clears them (and un-flags the tender if an
    earlier attempt had flagged it, as the QLD retry pass can).
    """
    global _current
    key = _key(ref)
    with _lock:
        if code == TENDER_PARTIAL:
            reasons = list(_pending) or _partials.get(key) or [(UNKNOWN, None)]
            _partials[key] = reasons
        else:
            _partials.pop(key, None)
        _pending.clear()
        _current = None


def note_tender_error(ref, exc: Exception) -> None:
    """A tender raised and was skipped. Filed under its ID if the scraper got that far."""
    global _current
    with _lock:
        key = _current or _key(ref)
        _partials[key] = [(SCRAPE_ERROR, type(exc).__name__)]
        _pending.clear()
        _current = None


def note_unfinished(exc: BaseException) -> None:
    """
    The run was stopped (a Cloudflare block, a dead browser, a crash) on a tender
    whose folder was already opened (note_current) but never reported. Its page
    text is on disk without its documents, so it is filed as a scrape error: the
    pipeline then stores it incomplete and fetches it again, instead of storing
    it as a finished tender with no documents. Nothing to do when no tender was
    part-way through.
    """
    global _current
    with _lock:
        if _current is None:
            return
        _partials[_current] = [(SCRAPE_ERROR, type(exc).__name__)]
        _pending.clear()
        _current = None


def note_late(ref, category: str, detail: str | None = None) -> None:
    """A problem found after scraping, while the tender's files were stored."""
    key = _key(ref)
    with _lock:
        reasons = _partials.setdefault(key, [])
        if reasons == [(UNKNOWN, None)]:
            reasons.clear()
        if (category, detail) not in reasons:
            reasons.append((category, detail))


# ---------------------------------------------------------------------------
# Tenders that were stored incomplete and are worth fetching again
# ---------------------------------------------------------------------------

# Problems another night's try can fix. A password-protected, unreadable, empty or
# oversized file will be just as bad next time, so those do not count.
RETRYABLE = (DOWNLOAD, PORTAL, SCRAPE_ERROR, NOT_STORED, UNKNOWN, PAGE)
# A tender is fetched again on at most this many later nights, then left alone, so
# a document that is gone for good cannot use up a site's request allowance every night.
MAX_INCOMPLETE_TRIES = 3
INCOMPLETE_KEY = "scrape_incomplete"
ATTEMPTS_KEY = "scrape_attempts"


def needs_another_try(ref) -> bool:
    """True if `ref` finished this run with a problem that fetching it again could fix."""
    with _lock:
        reasons = _partials.get(_key(ref)) or []
    return any(category in RETRYABLE for category, _ in reasons)


def flag_incomplete(raw_extra, prior_tries: int = 0) -> str:
    """
    `raw_extra` (a JSON string, a dict or None) with the incomplete flag and the
    number of tries so far added. Anything already in it is kept.
    """
    if isinstance(raw_extra, str):
        try:
            extra = json.loads(raw_extra)
        except ValueError:
            extra = {}
    else:
        extra = dict(raw_extra or {})
    if not isinstance(extra, dict):
        extra = {}
    extra[INCOMPLETE_KEY] = True
    extra[ATTEMPTS_KEY] = prior_tries + 1
    return json.dumps(extra, sort_keys=True)


def urls_to_treat_as_stored(rows, max_tries: int = MAX_INCOMPLETE_TRIES):
    """
    From (source_url, incomplete flag, tries) rows: (urls a resuming scraper may
    skip, {url: tries so far} for every flagged tender). A flagged tender with
    tries left is left out of the first set, so the scraper fetches it again.
    """
    skip, tries = set(), {}
    for url, flag, attempts in rows:
        if not url:
            continue
        flagged = str(flag).lower() == "true"
        if flagged:
            tries[url] = int(attempts or 0)
        if not flagged or int(attempts or 0) >= max_tries:
            skip.add(url)
    return skip, tries


class Snapshot:
    def __init__(self, partials, total):
        self.partials = partials  # {tender key: [(category, detail), ...]}
        self.total = total


def snapshot() -> Snapshot:
    with _lock:
        return Snapshot({k: list(v) for k, v in _partials.items()}, _total)


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------

def _tenders(n: int) -> str:
    return f"{n} tender" if n == 1 else f"{n} tenders"


def _one_tender(key: str, reasons) -> str:
    parts = []
    for category, detail in reasons:
        parts.append(f"{category} ({detail})" if detail else category)
    return f"{key}: " + ", ".join(parts)


def describe(code, count: int, notes: Snapshot, explanation: str,
             label: str, color: str) -> tuple[str, str, str]:
    """
    (status label, chip colour, message) for one scraper.

    `label`, `color` and `explanation` are what manager.explain_code() gave for
    the code; they are used unchanged for a failed run.
    """
    if code is None or code in FAILURE_CODES:
        return label, color, explanation

    partials = notes.partials
    n = len(partials)

    if n == 0 and code == TENDER_PARTIAL:
        # The scraper says something was incomplete but nobody recorded what.
        return ("Partial", "warning",
                "Some tenders were incomplete (reason not recorded); see the run logs.")

    if n == 0:
        total = notes.total
        if total and total > count:
            return "Success", "success", f"{count} of {_tenders(total)} scraped."
        if count == 0:
            return "Success", "success", "No new tenders to scrape."
        return "Success", "success", f"{_tenders(count)} scraped."

    scraped = max(count, n)
    head = f"{n} of {_tenders(scraped)} incomplete: "
    if n <= 3:
        body = "; ".join(_one_tender(k, v) for k, v in sorted(partials.items()))
    else:
        tally = Counter(category for reasons in partials.values()
                        for category in {c for c, _ in reasons})
        body = "; ".join(f"{times} x {category}" for category, times in tally.most_common())
    return "Partial", "warning", head + body + "."
