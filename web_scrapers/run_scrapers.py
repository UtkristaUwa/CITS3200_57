"""
The scraper stage, as the pipeline's manager function calls it.

This is a library, not a deployment. `manager.py` runs the whole daily
pipeline in one container -- scrape, then extract document text, then AI
processing -- so the scrapers are imported and called in-process:

    from web_scrapers.run_scrapers import run_scraper

    result = run_scraper(limit=10, output_dir=temp_dir)
    for folder in result.tender_dirs:
        ...

They no longer deploy or schedule themselves. There is no Cloud Run job and no
5am Cloud Scheduler entry belonging to this package: the manager is the thing
that is scheduled, and it decides when the scrape happens and what becomes of
the folders afterwards. See web_scrapers/INTEGRATION.md for the contract, and
for the teardown of the job that used to exist.

`run_scraper` writes the agreed layout -- one directory per tender, each with
its `<REF>.txt`, its `tender.json` and whatever documents were downloadable --
into `output_dir` and returns a ScrapeResult describing what landed there. It
uploads nothing: what happens to the folders is the manager's call.

A command line is kept for local development and for the live checks:

    python -m web_scrapers.run_scrapers --sources austender --limit 5
"""

import argparse
import logging
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from web_scrapers import common, storage

log = logging.getLogger("run_scrapers")

DEFAULT_SOURCES = "austender"


@dataclass
class ScrapeResult:
    """
    What one scrape produced.

    `tender_dirs` is the useful part: every directory under `output_dir` that
    holds a tender.json, which is exactly what the manager iterates. The rest
    exists so a caller can tell an empty scrape apart from a broken one --
    "austender returned nothing today" and "austender raised" both leave zero
    folders behind, and only one of them is worth waking someone up for.
    """

    output_dir: Path
    tender_dirs: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)   # source -> tenders written
    failed: dict = field(default_factory=dict)   # source -> why it raised
    skipped: dict = field(default_factory=dict)  # source -> why it never ran

    @property
    def total(self):
        return len(self.tender_dirs)

    @property
    def ok(self):
        """True when every requested source ran to completion."""
        return not self.failed and not self.skipped

    def summary(self):
        parts = [f"{self.total} tender(s)"]
        if self.counts:
            parts.append(
                "from " + ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items()))
            )
        if self.failed:
            parts.append("failed: " + ", ".join(sorted(self.failed)))
        if self.skipped:
            parts.append("skipped: " + ", ".join(sorted(self.skipped)))
        return "; ".join(parts)


def scrape_austender(limit, output_dir):
    from web_scrapers.austender import austender

    return austender.run_scraper(limit=limit, output_dir=output_dir)


def scrape_wa(limit, output_dir):
    from web_scrapers.wa_tenders import wa_tenders

    return wa_tenders.run_scraper(limit=limit, output_dir=output_dir)


def scrape_nt(limit, output_dir):
    from web_scrapers.nt_qtol import nt_qtol

    return nt_qtol.run_scraper(limit=limit, output_dir=output_dir)


def scrape_vic(limit, output_dir):
    from web_scrapers.vic_buyingfor import vic_buyingfor

    return vic_buyingfor.run_scraper(limit=limit, output_dir=output_dir)


def scrape_qld(limit, output_dir):
    from web_scrapers.qld_qtenders import qld_qtenders

    # QTenders paginates its search separately from the tender cap: LIMIT trims
    # the list only after every page has been walked, so MAX_PAGES is what
    # actually shortens a run. It is read here rather than in the scraper's own
    # main() so it works through this entrypoint too.
    return qld_qtenders.run_scraper(
        limit=limit,
        output_dir=output_dir,
        max_pages=int(os.environ.get("MAX_PAGES", "0")),
    )


# These two portals render their results client-side and sit behind Cloudflare,
# so they need a real Chrome; austender is plain HTTP and needs none of this.
BROWSER_SOURCES = {"vic", "qld"}

SCRAPERS = {
    "austender": scrape_austender,
    "wa": scrape_wa,
    "nt": scrape_nt,
    "vic": scrape_vic,
    "qld": scrape_qld,
}


def _start_display():
    """
    Start an Xvfb virtual display, or return None to fall back to headless.

    Started from Python rather than by wrapping the entrypoint in `xvfb-run`:
    the wrapper gives no output if it fails to bring the server up, so a
    failure looks identical to a slow scrape. Here it is one log line either
    way, and a failure degrades to headless instead of hanging the run.
    """
    try:
        from sbvirtualdisplay import Display

        # use_xauth=False keeps this working without the xauth binary, which
        # the xvfb package does not pull in.
        display = Display(visible=False, size=(1920, 1080), use_xauth=False)
        display.start()
    except Exception as exc:
        log.warning(
            "could not start a virtual display (%s) -- Chrome will run headless, "
            "which Cloudflare challenges more aggressively",
            exc,
        )
        return None

    log.info("virtual display started (DISPLAY=%s)", os.environ.get("DISPLAY"))
    return display


@contextmanager
def virtual_display(sources):
    """Provide a virtual display when a containerised run needs a browser."""
    if not (common.in_container() and set(sources) & BROWSER_SOURCES):
        yield
        return

    display = _start_display()
    try:
        yield
    finally:
        if display is not None:
            try:
                display.stop()
            except Exception:
                log.debug("virtual display did not stop cleanly", exc_info=True)


def parse_sources(value):
    """Split and validate the SOURCES / --sources list."""
    names = [name.strip().lower() for name in (value or "").split(",") if name.strip()]
    unknown = [name for name in names if name not in SCRAPERS]
    if unknown:
        raise ValueError(
            f"unknown source(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(SCRAPERS))}"
        )
    return names or [DEFAULT_SOURCES]


def unrunnable(sources):
    """
    Which of these sources cannot run here, and why.

    Only one thing can rule a source out today: it drives a real browser and
    there is no browser installed. Checking up front turns a Selenium
    exception three layers down into one line in the run summary, which
    matters because the manager's image is a plain python:slim with no Chrome
    in it -- the failure would otherwise look like the portal's fault.
    """
    if not set(sources) & BROWSER_SOURCES or common.chrome_available():
        return {}
    return {name: common.NO_CHROME_REASON for name in sources if name in BROWSER_SOURCES}


def tender_directories(output_dir):
    """Every tender folder under `output_dir`, identified by its tender.json."""
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return []
    return sorted(
        path for path in output_dir.iterdir()
        if path.is_dir() and (path / common.RECORD_NAME).exists()
    )


def run(sources, limit, output_dir):
    """
    Run each named scraper into `output_dir` and report what landed.

    A source that fails outright is recorded and the remaining sources still
    run -- one broken portal must not cost us the others. Nothing is uploaded
    and nothing is deleted; the folders are the output.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = ScrapeResult(output_dir=output_dir, skipped=unrunnable(sources))
    for name, reason in result.skipped.items():
        log.error("%s skipped: %s", name, reason)

    seen = {path.name for path in tender_directories(output_dir)}
    for name in sources:
        if name in result.skipped:
            continue
        log.info("--- %s ---", name)
        try:
            SCRAPERS[name](limit, output_dir)
        except Exception as exc:
            result.failed[name] = f"{exc.__class__.__name__}: {exc}"
            log.exception("%s failed: %s", name, exc)
            continue

        # Counted from what is actually on disk rather than from what the
        # scraper says it returned: the folder is the deliverable, and a
        # record with no folder behind it would be a lie to the next stage.
        written = [p for p in tender_directories(output_dir) if p.name not in seen]
        seen.update(path.name for path in written)
        result.counts[name] = len(written)
        log.info("%s: %d tender(s)", name, len(written))

    result.tender_dirs = tender_directories(output_dir)
    log.info("scrape finished -- %s", result.summary())
    return result


def run_scraper(limit=10, output_dir=None, sources=None):
    """
    Scrape into `output_dir`, one directory per tender. The manager's entrypoint.

    `sources` accepts a list or a comma-separated string and defaults to the
    SOURCES environment variable, then to austender. `limit` is per source;
    0 means no cap.

    Returns a ScrapeResult. Raises only on a caller error (an unknown source
    name) -- a portal being down is reported in the result, not raised, because
    the pipeline should process the sources that did work.
    """
    if not isinstance(sources, (list, tuple)):
        sources = parse_sources(sources or os.environ.get("SOURCES", DEFAULT_SOURCES))
    else:
        sources = parse_sources(",".join(sources))

    output_dir = Path(output_dir) if output_dir else common.DEFAULT_OUTPUT_DIR

    with virtual_display(sources):
        return run(sources, limit, output_dir)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--sources",
        default=os.environ.get("SOURCES", DEFAULT_SOURCES),
        help="comma-separated: austender,wa,nt,vic,qld",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("LIMIT", "10")),
        help="max tenders per source (0 = no limit)",
    )
    parser.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR"))
    parser.add_argument(
        "--publish",
        action="store_true",
        default=bool(os.environ.get("OUTPUT_BUCKET")),
        help=(
            "also mirror the folders to OUTPUT_BUCKET. Off by default and not "
            "part of the manager's path -- the pipeline decides what to do "
            "with the folders."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s"
    )
    log.info("starting: sources=%s limit=%s", args.sources, args.limit)
    # httpx logs every request at INFO, which drowns out the scrape progress.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def go(directory):
        result = run_scraper(
            limit=args.limit, output_dir=directory, sources=args.sources
        )
        if args.publish:
            storage.publish(result.output_dir, os.environ.get("OUTPUT_PREFIX", storage.DEFAULT_PREFIX))
        return result

    if args.output_dir:
        result = go(args.output_dir)
    else:
        # Nothing here keeps the output: a run with no --output-dir is a smoke
        # test. The pipeline always passes its own directory.
        with tempfile.TemporaryDirectory() as temp_dir:
            log.info("working directory: %s", temp_dir)
            result = go(temp_dir)

    print(result.summary())
    return 1 if (result.failed or result.skipped) and result.total == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
