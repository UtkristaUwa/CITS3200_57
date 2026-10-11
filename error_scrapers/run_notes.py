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

import re
import threading
from collections import Counter

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
UNKNOWN = "reason not recorded"

_lock = threading.RLock()
_partials: dict[str, list[tuple[str, str | None]]] = {}
_pending: list[tuple[str, str | None]] = []
_total: int | None = None


def _key(ref) -> str:
    """Same folder-safe form the pipeline uses, so stage 1 and stage 2 agree."""
    name = str(ref).replace("/", "_").replace("\\", "_")
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip() or "unknown"


def reset() -> None:
    """Start of a scraper's run."""
    global _total
    with _lock:
        _partials.clear()
        _pending.clear()
        _total = None


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
    key = _key(ref)
    with _lock:
        if code == TENDER_PARTIAL:
            reasons = list(_pending) or _partials.get(key) or [(UNKNOWN, None)]
            _partials[key] = reasons
        else:
            _partials.pop(key, None)
        _pending.clear()


def note_tender_error(ref, exc: Exception) -> None:
    """A tender raised and was skipped."""
    key = _key(ref)
    with _lock:
        _partials[key] = [(SCRAPE_ERROR, type(exc).__name__)]
        _pending.clear()


def note_late(ref, category: str, detail: str | None = None) -> None:
    """A problem found after scraping, while the tender's files were stored."""
    key = _key(ref)
    with _lock:
        reasons = _partials.setdefault(key, [])
        if reasons == [(UNKNOWN, None)]:
            reasons.clear()
        if (category, detail) not in reasons:
            reasons.append((category, detail))


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
