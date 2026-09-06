"""
Entrypoint for the scraper stage -- what the Cloud Run job runs.

Scrapes the configured sources into one directory per tender and, when
OUTPUT_BUCKET is set, mirrors the result to Cloud Storage.

Configuration (all optional, all via environment so Cloud Run can set them):
    SOURCES        comma-separated: austender,vic,qld   (default: austender)
    LIMIT          max tenders per source (default 10; 0 = no limit)
    OUTPUT_DIR     where to write (default: a temporary directory)
    OUTPUT_BUCKET  GCS bucket to mirror results into (default: none -- the
                   local directory is the primary output either way)
    OUTPUT_PREFIX  prefix within that bucket (default: raw)

Usage:
    python -m web_scrapers.run_scrapers --sources austender --limit 5
"""

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from web_scrapers import storage

log = logging.getLogger("run_scrapers")

DEFAULT_SOURCES = "austender"


def scrape_austender(limit, output_dir):
    from web_scrapers.austender import austender

    return austender.run_scraper(limit=limit, output_dir=output_dir)


def scrape_vic(limit, output_dir):
    from web_scrapers.vic_buyingfor import vic_buyingfor

    return vic_buyingfor.run_scraper(limit=limit, output_dir=output_dir)


def scrape_qld(limit, output_dir):
    from web_scrapers.qld_qtenders import qld_qtenders

    return qld_qtenders.run_scraper(limit=limit, output_dir=output_dir)


# The browser-driven scrapers need Chrome in the image; austender does not.
SCRAPERS = {
    "austender": scrape_austender,
    "vic": scrape_vic,
    "qld": scrape_qld,
}


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


def run(sources, limit, output_dir, prefix=storage.DEFAULT_PREFIX):
    """
    Run each named scraper into `output_dir`, then publish.

    Returns the number of tenders scraped. A source that fails outright is
    logged and the remaining sources still run -- one broken portal must not
    cost us the others.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    total, failed = 0, []
    for name in sources:
        log.info("--- %s ---", name)
        try:
            records = SCRAPERS[name](limit, output_dir)
            log.info("%s: %d tender(s)", name, len(records))
            total += len(records)
        except Exception as exc:
            failed.append(name)
            log.exception("%s failed: %s", name, exc)

    storage.publish(output_dir, prefix)

    log.info("scraped %d tender(s) from %d source(s)", total, len(sources) - len(failed))
    if failed:
        log.error("failed source(s): %s", ", ".join(failed))
    return total, failed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--sources",
        default=os.environ.get("SOURCES", DEFAULT_SOURCES),
        help="comma-separated: austender,vic,qld",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("LIMIT", "10")),
        help="max tenders per source (0 = no limit)",
    )
    parser.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR"))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s"
    )
    # httpx logs every request at INFO, which drowns out the scrape progress.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    sources = parse_sources(args.sources)
    prefix = os.environ.get("OUTPUT_PREFIX", storage.DEFAULT_PREFIX)

    if args.output_dir:
        total, failed = run(sources, args.limit, args.output_dir, prefix)
    else:
        # No persistent disk on Cloud Run: scrape into a temp dir, publish, drop it.
        with tempfile.TemporaryDirectory() as temp_dir:
            log.info("working directory: %s", temp_dir)
            total, failed = run(sources, args.limit, temp_dir, prefix)

    return 1 if failed and total == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
