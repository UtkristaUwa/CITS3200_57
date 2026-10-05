"""
buy.nsw crawl order.

buy.nsw's bot-protection WAF answers HTTP 202 (no page) after a handful of
requests, so one round cannot read the whole site. The crawl therefore:

  1. starts at a saved listing page (the cursor),
  2. skips every tender whose detail URL is already in the DB (a set lookup,
     no request),
  3. scrapes the ones that aren't,
  4. stops the moment the WAF answers 202,
  5. optionally waits out the block (cooldown) and resumes at the same spot,
     up to `retries_after_block` times, then
  6. reports where to resume. After the last page the cursor wraps to page 1.

The functions that touch the network are passed in, so this file imports
nothing from scraper.py and can be debugged on its own.
"""

import time
from dataclasses import dataclass, field

from error_scrapers import common

MAX_PAGES = 200  # safety valve if the site ever repeats its last page forever


@dataclass
class CrawlResult:
    tenders: list = field(default_factory=list)
    codes: list = field(default_factory=list)   # status code per tender scraped
    blocked: bool = False                       # the WAF stopped us for good this run
    next_page: int = 1                          # where the next run starts
    pages: int = 0                              # listing pages fetched
    skipped: int = 0                            # tenders already in the DB
    waits: int = 0                              # cooldowns we sat through


def _short(url):
    return url.rstrip("/").rsplit("/", 1)[-1][:8]


def crawl(fetch_page, scrape_one, known_urls, start_page, limit, log, res,
          retries_after_block=0, cooldown_seconds=0, sleep=time.sleep):
    """
    fetch_page(page) -> (http_status, [detail urls])
    scrape_one(url)  -> (status_code, tender_dict_or_empty); SITE_RATE_LIMITED means 202
    Fills and returns `res`.
    """
    page = start_page
    res.next_page = start_page
    rounds_left = retries_after_block
    log.info("crawl: starting at listing page %d (%d tender URL(s) already in the DB)",
             page, len(known_urls))

    def wait_out_block(what):
        """True if we waited and should retry the same request."""
        nonlocal rounds_left
        if rounds_left <= 0:
            return False
        rounds_left -= 1
        res.waits += 1
        log.warning("crawl: %s -> WAF block. Waiting %d min, then retrying "
                    "(%d retr%s left after this)", what, cooldown_seconds // 60,
                    rounds_left, "y" if rounds_left == 1 else "ies")
        sleep(cooldown_seconds)
        log.info("crawl: cooldown over, retrying")
        return True

    while True:
        if page > MAX_PAGES:
            log.warning("crawl: passed page %d -> restarting at page 1 next run", MAX_PAGES)
            res.next_page = 1
            return res

        status, links = fetch_page(page)
        res.pages += 1
        if status != 200:
            if wait_out_block(f"listing page {page} answered HTTP {status}"):
                continue
            log.warning("crawl: listing page %d answered HTTP %s -> stopping; "
                        "next run resumes at page %d", page, status, page)
            res.blocked, res.next_page = True, page
            return res
        if not links:
            log.info("crawl: listing page %d is empty -> end of the list; "
                     "next run restarts at page 1", page)
            res.next_page = 1
            return res

        known_here = sum(1 for u in links if u in known_urls)
        res.skipped += known_here
        log.info("crawl: page %d -> %d link(s), %d already in the DB, %d new",
                 page, len(links), known_here, len(links) - known_here)

        for url in links:
            if url in known_urls:
                log.debug("crawl:   %s in DB -> moving on", _short(url))
                continue
            log.debug("crawl:   %s not in DB -> scraping", _short(url))
            code, tender = scrape_one(url)
            while code == common.SITE_RATE_LIMITED and wait_out_block(f"{_short(url)} answered 202"):
                code, tender = scrape_one(url)
            if code == common.SITE_RATE_LIMITED:
                log.warning("crawl:   %s WAF answered 202 -> stopping here, finishing what "
                            "we have; next run resumes at page %d", _short(url), page)
                res.blocked, res.next_page = True, page
                return res
            res.codes.append(code)
            if tender:
                res.tenders.append(tender)
            if limit and len(res.tenders) >= limit:
                log.info("crawl: reached the limit of %d new tender(s) -> stopping; "
                         "next run resumes at page %d", limit, page)
                res.next_page = page
                return res
        page += 1
        res.next_page = page