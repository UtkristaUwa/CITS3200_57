"""
error_scrapers/reporting.py

Output logging for the scrapers -- the only thing this file does.
Every scraper logs through one helper so a Cloud Run log reads the same for every
site and you can tell at a glance which site a line came from.

    site_logger("GRANTCONNECT")   logger that prefixes every line with the site tag
    @reported("GRANTCONNECT")     START / END banner around a run_scraper(); the
                                  return value is passed through UNCHANGED
    tender_line(...)              one result line per tender
    tender_failed(...)            one error line when a tender raised
    documents_line(...)           "documents: 3/4 saved" (warns when saved < advertised)
    diagnose(...)                 end-of-run verdict: what the pattern of codes means
    log_run_summary(...)          one table at the end, one row per site (manager.py)
    configure_logging()           for running a scraper by hand
"""

import functools
import inspect
import logging
import time

from error_scrapers import common

TAG_WIDTH = 13  # len("VIC_BUYINGFOR"); keeps
RULE = "=" * 78

CODE_NAMES = {
    common.SITE_SUCCESS: "SITE_SUCCESS",
    common.SITE_TOTAL_FAILURE: "SITE_TOTAL_FAILURE",
    common.SITE_LOGIN_FAILED: "SITE_LOGIN_FAILED",
    common.SITE_BOT_BLOCKED: "SITE_BOT_BLOCKED",
    common.SITE_STRUCTURE_CHANGE: "SITE_STRUCTURE_CHANGE",
    common.SITE_RATE_LIMITED: "SITE_RATE_LIMITED",
    common.TENDER_PARTIAL: "TENDER_PARTIAL",
}


def code_name(code) -> str:
    return CODE_NAMES.get(code, f"UNKNOWN({code})")


# ---------------------------------------------------------------------------
# The per-site logger
# ---------------------------------------------------------------------------

class _SiteAdapter(logging.LoggerAdapter):
    """Puts [SITE] at the start of every message, whatever the root format is."""

    def process(self, msg, kwargs):
        return f"[{self.extra['site']:<{TAG_WIDTH}}] {msg}", kwargs


def site_logger(site: str) -> logging.LoggerAdapter:
    return _SiteAdapter(logging.getLogger(f"scraper.{site.lower()}"), {"site": site})


def configure_logging(level=logging.INFO) -> None:
    """For running a scraper by hand. manager.py sets up its own logging."""
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")


# ---------------------------------------------------------------------------
# START / END banner around a whole run
# ---------------------------------------------------------------------------

def reported(site: str):
    """
    Decorator for a scraper's run_scraper(), which must return a
    common.ScrapeResult(code, site, count). Logs a START banner, runs the
    scraper, then logs an END banner with the code, site, count and time.

    The ScrapeResult is passed through untouched, and an exception is logged
    with its traceback and re-raised, so nothing downstream changes.
    """

    def decorate(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            log = site_logger(site)
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            limit = bound.arguments.get("limit")
            output_dir = bound.arguments.get("output_dir")

            log.info(RULE)
            log.info(f"START  limit={limit or 'none (every tender)'}  output_dir={output_dir}")
            started = time.monotonic()

            try:
                result = fn(*args, **kwargs)
            except Exception:
                log.exception(f"CRASHED after {time.monotonic() - started:.1f}s")
                log.info(RULE)
                raise

            if not isinstance(result, common.ScrapeResult):
                log.error(f"run_scraper returned {type(result).__name__}, expected "
                          "common.ScrapeResult(code, site, count)")
                log.info(RULE)
                raise TypeError("run_scraper must return common.ScrapeResult")

            say = log.info if result.code == common.SITE_SUCCESS else (
                log.warning if result.code == common.TENDER_PARTIAL else log.error
            )
            say(
                f"END    code={result.code} {code_name(result.code)}  "
                f"site={result.site}  tenders={result.count}  "
                f"time={time.monotonic() - started:.1f}s"
            )
            log.info(RULE)
            return result

        return wrapper

    return decorate


# ---------------------------------------------------------------------------
# Per-tender lines
# ---------------------------------------------------------------------------

def tender_line(log, index: int, total: int, ref: str, code: int) -> None:
    """(2/30) GO8232  SITE_SUCCESS   -- warning/error level when it isn't a success."""
    say = log.info if code == common.SITE_SUCCESS else (
        log.warning if code == common.TENDER_PARTIAL else log.error
    )
    say(f"({index}/{total}) {ref}  {code_name(code)}")


def tender_failed(log, index: int, total: int, url: str, exc: Exception) -> None:
    """A tender raised. The run carries on; this is the only record of why."""
    log.error(f"({index}/{total}) FAILED  {url}  ({type(exc).__name__}: {exc})")


def documents_line(log, saved: int, advertised: int | None = None) -> None:
    """
    documents: 3/4 saved   (advertised known)
    documents: 3 saved     (advertised unknown, e.g. a zip package)
    documents: none attached

    Warns when fewer were saved than the page advertised, so a tender that
    "succeeded" but lost a file is visible.
    """
    if advertised is None:
        text = f"documents: {saved} saved" if saved else "documents: none attached"
        log.info(f"       {text}")
        return
    if advertised == 0:
        log.info("       documents: none attached")
    elif saved < advertised:
        log.warning(f"       documents: {saved}/{advertised} saved  "
                    f"({advertised - saved} FAILED -- see the lines above)")
    else:
        log.info(f"       documents: {saved}/{advertised} saved")


# ---------------------------------------------------------------------------
# End-of-run verdict
# ---------------------------------------------------------------------------

def diagnose(log, codes: list, logged_in: bool = False, partial_is_expected: bool = False) -> None:
    """
    Say in words what the pattern of per-tender codes means. Call it once,
    after the loop, with every tender's code in `codes`. It catches the cases
    that look like success but aren't: zero tenders, or every tender partial
    or unparseable. Pass partial_is_expected=True for a site whose documents
    are always out of reach by design (QLD), so all-partial is a warning, not
    an error.
    """
    total = len(codes)
    if total == 0:
        log.error("verdict: the listing returned 0 tenders -- the site may be down, "
                  "blocking us, or its layout has changed")
        return

    counts = {}
    for code in codes:
        counts[code] = counts.get(code, 0) + 1
    breakdown = ", ".join(f"{n} x {code_name(c)}" for c, n in sorted(counts.items()))
    log.info(f"results: {total} tender(s) -- {breakdown}")

    if counts.get(common.SITE_STRUCTURE_CHANGE) == total:
        log.error("verdict: EVERY tender page failed to parse -- the site's layout "
                  "has almost certainly changed; the selectors need updating")
    elif counts.get(common.TENDER_PARTIAL) == total:
        if partial_is_expected:
            log.warning(f"verdict: all {total} tender(s) were partial -- expected: "
                        "their documents need a login this scraper does not have")
        elif logged_in:
            log.error(f"verdict: all {total} tender(s) were partial although login "
                      "succeeded -- document downloads are failing; check the "
                      "session, permissions and the download warnings above")
        else:
            log.error(f"verdict: all {total} tender(s) were partial -- every "
                      "document step failed; check the warnings above")
    elif counts.get(common.TENDER_PARTIAL):
        log.warning(f"verdict: {counts[common.TENDER_PARTIAL]} of {total} tender(s) "
                    "were partial -- see the warnings above for which documents failed")
    elif counts.get(common.SITE_SUCCESS) == total:
        log.info(f"verdict: all {total} tender(s) scraped cleanly")


# --------------------------------------------------------------------------- 
# End-of-run table (manager.py)
# ---------------------------------------------------------------------------

def log_run_summary(rows, logger) -> None:
    """
    rows: (site, code, tenders_scraped, seconds) per scraper. code is None when
    the scraper raised or returned nothing usable.
    """
    logger.info(RULE)
    logger.info("SCRAPE RUN SUMMARY")
    logger.info(f"{'SITE':<14} {'CODE':<26} {'TENDERS':>7} {'TIME':>8}")
    for site, code, count, seconds in rows:
        outcome = "RAISED / NO RESULT" if code is None else f"{code} {code_name(code)}"
        logger.info(f"{site:<14} {outcome:<26} {count:>7} {seconds:>7.1f}s")
    logger.info(RULE)