"""
Dry run of the buy.nsw crawl that prints its thoughts.

    python3 -m error_scrapers.buy_nsw.debug_crawl [start_page] [max_new_tenders]

Reads known URLs from BigQuery; writes nothing.
"""

import logging
import sys
import time

import httpx

from error_scrapers import common
from error_scrapers.buy_nsw import crawl, scraper as s


def main():
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "urllib3", "google", "google.auth"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log = logging.getLogger("debug")

    from ingestion.bigquery_client import get_client, TENDERS_TABLE
    rows = get_client().query(
        f"SELECT DISTINCT source_url FROM `{TENDERS_TABLE}` "
        "WHERE source_id = 'buynsw' AND source_url IS NOT NULL").result()
    known = {r.source_url for r in rows}
    log.info("checking the DB: %d buy.nsw URL(s) stored, e.g. %s", len(known), list(known)[:2])

    t0 = time.time()
    with httpx.Client(follow_redirects=True) as client:
        def scrape_one(url):
            time.sleep(s.REQUEST_DELAY_SECONDS)
            r = s._get_with_retry(client, url, retries=1, headers=s.HEADERS, timeout=30.0)
            log.info("        detail page +%ds -> HTTP %s", time.time() - t0, r.status_code)
            if r.status_code == 202:
                return common.SITE_RATE_LIMITED, {}
            fields, code = s.parse_detail(r.text)
            log.info("        read OK: %s (%s)", fields.get("opportunity_id"), fields.get("title"))
            return code, {"opportunity_id": fields.get("opportunity_id")}

        retries = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        cooldown = int(sys.argv[4]) if len(sys.argv) > 4 else 360
        res = crawl.crawl(lambda p: s._fetch_listing_page(client, p), scrape_one,
                          known, start, limit, log, crawl.CrawlResult(),
                          retries_after_block=retries, cooldown_seconds=cooldown)
    print(f"\nDONE: blocked={res.blocked}  pages={res.pages}  already-in-DB={res.skipped}  "
          f"new-tenders-read={len(res.tenders)}  next-run-resumes-at-page={res.next_page}  "
          f"time={time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()