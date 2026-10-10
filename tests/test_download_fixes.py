"""
Download waiting and the QLD (VendorPanel) follow/download fixes.

Evidence (run of 10 Oct): 7 of 100 QLD packages failed. Two raised the browser
driver's 120s ReadTimeoutError on the Download click; five waited the full 180s
and saw no file. Two tenders were followed but did not show as followed.
These tests use a fake browser and a temp folder, so nothing touches a portal.
"""

import os
import threading
import time

import pytest

from error_scrapers import common
from error_scrapers.qld_qtenders import scraper as qld


# ---------------------------------------------------------------------------
# common.wait_for_download
# ---------------------------------------------------------------------------

def _later(delay, fn):
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()
    return t


def test_returns_a_file_that_appears(tmp_path):
    _later(0.1, lambda: (tmp_path / "pkg.zip").write_bytes(b"zip"))
    assert common.wait_for_download(str(tmp_path), set(), 3, poll=0.02) == "pkg.zip"


def test_ignores_files_that_were_already_there(tmp_path):
    (tmp_path / "old.zip").write_bytes(b"x")
    started = time.monotonic()
    assert common.wait_for_download(str(tmp_path), {"old.zip"}, 0.2, poll=0.02) is None
    assert time.monotonic() - started < 2


def test_gives_up_when_nothing_arrives(tmp_path):
    assert common.wait_for_download(str(tmp_path), set(), 0.2, poll=0.02) is None


def test_a_partial_download_keeps_the_wait_alive_past_max_wait(tmp_path):
    # ACT shows a .crdownload from second 0 and finishes much later.
    (tmp_path / "pkg.zip.crdownload").write_bytes(b"1")

    def finish():
        (tmp_path / "pkg.zip.crdownload").unlink()
        (tmp_path / "pkg.zip").write_bytes(b"done")

    _later(0.6, finish)
    got = common.wait_for_download(str(tmp_path), set(), 0.2, poll=0.02,
                                   stall_seconds=5, hard_max=10)
    assert got == "pkg.zip"


def test_a_stalled_partial_download_gives_up(tmp_path):
    (tmp_path / "pkg.zip.crdownload").write_bytes(b"stuck")
    started = time.monotonic()
    assert common.wait_for_download(str(tmp_path), set(), 0.2, poll=0.02,
                                    stall_seconds=0.3, hard_max=10) is None
    assert time.monotonic() - started < 3


def test_a_growing_partial_is_not_a_stall(tmp_path):
    partial = tmp_path / "pkg.zip.crdownload"
    partial.write_bytes(b"1")

    def grow():
        for i in range(10):
            partial.write_bytes(b"x" * (i + 2))
            time.sleep(0.08)
        partial.unlink()
        (tmp_path / "pkg.zip").write_bytes(b"done")

    threading.Thread(target=grow, daemon=True).start()
    got = common.wait_for_download(str(tmp_path), set(), 0.2, poll=0.02,
                                   stall_seconds=0.3, hard_max=10)
    assert got == "pkg.zip"


def test_the_hard_max_stops_a_download_that_keeps_growing(tmp_path):
    partial = tmp_path / "pkg.zip.crdownload"
    partial.write_bytes(b"1")
    stop = threading.Event()

    def grow():
        n = 2
        while not stop.is_set():
            partial.write_bytes(b"x" * n)
            n += 1
            time.sleep(0.02)

    threading.Thread(target=grow, daemon=True).start()
    started = time.monotonic()
    try:
        assert common.wait_for_download(str(tmp_path), set(), 0.2, poll=0.02,
                                        stall_seconds=5, hard_max=0.6) is None
    finally:
        stop.set()
    assert time.monotonic() - started < 3


def test_a_cancelled_download_whose_partial_vanishes_gives_up(tmp_path):
    (tmp_path / "pkg.zip.crdownload").write_bytes(b"1")
    _later(0.1, lambda: (tmp_path / "pkg.zip.crdownload").unlink())
    started = time.monotonic()
    assert common.wait_for_download(str(tmp_path), set(), 30, poll=0.02,
                                    stall_seconds=30, hard_max=30) is None
    assert time.monotonic() - started < 3


def test_a_download_that_never_starts_is_given_max_wait_only(tmp_path):
    started = time.monotonic()
    assert common.wait_for_download(str(tmp_path), set(), 0.3, poll=0.02,
                                    stall_seconds=30, hard_max=30) is None
    assert 0.25 < time.monotonic() - started < 3


# ---------------------------------------------------------------------------
# QLD VendorPanelSession.download_package
# ---------------------------------------------------------------------------

class ReadTimeoutError(Exception):
    """Same name as urllib3's, which is what the driver raised in the 10 Oct run."""


class FakeBrowser:
    def __init__(self, downloads_dir, on_click):
        self.downloads_dir = downloads_dir
        self.on_click = on_click
        self.opened = []
        self.clicks = 0

    def open(self, url):
        self.opened.append(url)

    def wait_for_element(self, selector, timeout=None):
        return True

    def click(self, selector):
        self.clicks += 1
        return self.on_click(self, self.clicks)

    def get_page_source(self):
        return "<html></html>"

    def is_element_present(self, selector):
        return False


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(qld, "DOWNLOAD_WAIT_SECONDS", 0.3)
    monkeypatch.setattr(qld, "DOWNLOAD_POLL_SECONDS", 0.02)
    monkeypatch.setattr(qld, "DOWNLOAD_ATTEMPTS", 2)
    downloads = tmp_path / "downloaded_files"
    downloads.mkdir()
    dest = tmp_path / "VP1"
    dest.mkdir()

    def make(on_click):
        s = qld.VendorPanelSession()
        s._downloads_dir = str(downloads)
        s.sb = FakeBrowser(str(downloads), on_click)
        return s

    return make, downloads, dest


def test_a_normal_download_is_moved_into_the_tender_folder(session):
    make, downloads, dest = session

    def click(browser, n):
        (downloads / "VP1.zip").write_bytes(b"zip")

    s = make(click)
    path = s.download_package("1", str(dest))
    assert os.path.basename(path) == "VP1.zip"
    assert os.path.exists(path)
    assert not (downloads / "VP1.zip").exists()
    assert s.sb.clicks == 1


def test_a_click_that_times_out_still_collects_a_file_that_arrives_later(session):
    make, downloads, dest = session

    def click(browser, n):
        _later(0.1, lambda: (downloads / "VP1.zip").write_bytes(b"zip"))
        raise ReadTimeoutError("Read timed out. (read timeout=120)")

    s = make(click)
    path = s.download_package("1", str(dest))
    assert os.path.basename(path) == "VP1.zip"
    assert s.sb.clicks == 1          # no need to retry: the file did arrive


def test_no_file_after_the_first_try_triggers_one_fresh_try(session):
    make, downloads, dest = session

    def click(browser, n):
        if n == 2:
            (downloads / "VP1.zip").write_bytes(b"zip")

    s = make(click)
    path = s.download_package("1", str(dest))
    assert os.path.basename(path) == "VP1.zip"
    assert s.sb.clicks == 2
    assert len(s.sb.opened) == 2     # the package page was reopened


def test_a_file_from_the_first_try_that_lands_late_is_still_accepted(session):
    make, downloads, dest = session

    def click(browser, n):
        if n == 1:
            _later(0.4, lambda: (downloads / "VP1.zip").write_bytes(b"zip"))

    s = make(click)
    path = s.download_package("1", str(dest))
    assert os.path.basename(path) == "VP1.zip"


def test_it_gives_up_after_the_attempts_are_used(session):
    make, downloads, dest = session
    s = make(lambda browser, n: None)
    with pytest.raises(TimeoutError, match="did not finish"):
        s.download_package("1", str(dest))
    assert s.sb.clicks == 2


def test_a_click_error_that_is_not_a_timeout_is_not_swallowed(session):
    make, downloads, dest = session

    def click(browser, n):
        raise RuntimeError("element not found")

    s = make(click)
    with pytest.raises(RuntimeError):
        s.download_package("1", str(dest))


def test_timeout_detection():
    assert qld.is_timeout_error(ReadTimeoutError("x"))
    assert qld.is_timeout_error(TimeoutError("x"))
    assert not qld.is_timeout_error(RuntimeError("x"))


# ---------------------------------------------------------------------------
# QLD VendorPanelSession.follow
# ---------------------------------------------------------------------------

@pytest.fixture
def follow_session(monkeypatch):
    monkeypatch.setattr(qld, "RELOAD_SECONDS", 0)
    monkeypatch.setattr(qld, "FOLLOW_CHECK_PAUSE", 0)
    monkeypatch.setattr(qld, "FOLLOW_CHECKS", 6)

    def make(rows):
        """rows: what successive find_row calls return."""
        s = qld.VendorPanelSession()
        s.sb = FakeBrowser("", lambda b, n: None)
        calls = iter(rows)
        s.find_row = lambda number: next(calls, rows[-1])
        return s

    return make


def _row(followed, can_follow):
    return {"followed": followed, "can_follow": can_follow, "can_download": followed, "documents": 3}


def test_follow_succeeds_first_time(follow_session):
    s = follow_session([_row(True, False)])
    assert s.follow("1") is True
    assert s.sb.clicks == 1


def test_follow_waits_for_a_slow_portal(follow_session):
    # Still says Follow for two looks (the 10 Oct failures), then Unfollow.
    s = follow_session([_row(False, True), _row(False, True), _row(False, True), _row(True, False)])
    assert s.follow("1") is True


def test_follow_clicks_a_second_time_only_once(follow_session):
    s = follow_session([_row(False, True)] * 6)
    assert s.follow("1") is False
    assert s.sb.clicks == 2          # the first click plus exactly one more


def test_follow_never_clicks_again_when_the_row_has_no_follow_link(follow_session):
    # Row exists but offers neither Follow nor Unfollow: do not click blindly.
    s = follow_session([_row(False, False)] * 6)
    assert s.follow("1") is False
    assert s.sb.clicks == 1


def test_follow_is_false_if_the_row_disappears(follow_session):
    s = follow_session([None] * 6)
    assert s.follow("1") is False


def test_find_row_looks_again_when_the_filter_misses_once(monkeypatch):
    monkeypatch.setattr(qld, "FIND_ATTEMPTS", 2)
    s = qld.VendorPanelSession()
    s.sb = FakeBrowser("", lambda b, n: None)
    s.sb.type = lambda selector, text: None
    waits = []

    def wait_for_element(selector, timeout=None):
        waits.append(selector)
        if len(waits) == 1:
            raise RuntimeError("not there")
        return True

    s.sb.wait_for_element = wait_for_element
    opened = []
    s._open_list = lambda: (opened.append(1), setattr(s, "_on_list", True))
    monkeypatch.setattr(qld, "parse_row", lambda html, number: {"followed": True})
    s._on_list = True
    assert s.find_row("1") == {"followed": True}
    assert len(waits) == 2
    assert opened == [1]             # the list was reloaded between the tries


def test_find_row_gives_up_after_the_attempts(monkeypatch):
    monkeypatch.setattr(qld, "FIND_ATTEMPTS", 2)
    s = qld.VendorPanelSession()
    s.sb = FakeBrowser("", lambda b, n: None)
    s.sb.type = lambda selector, text: None

    def never(selector, timeout=None):
        raise RuntimeError("no")

    s.sb.wait_for_element = never
    s._open_list = lambda: setattr(s, "_on_list", True)
    s._on_list = True
    assert s.find_row("1") is None


# ---------------------------------------------------------------------------
# ACT and VIC use the same helper
# ---------------------------------------------------------------------------

def test_act_download_waits_through_a_slow_partial_and_moves_the_zip(tmp_path, monkeypatch):
    from error_scrapers.tenders_act import browser as act
    monkeypatch.setattr(act, "DOWNLOAD_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(act, "DOWNLOAD_STALL_SECONDS", 5)
    monkeypatch.setattr(act, "DOWNLOAD_HARD_MAX_SECONDS", 10)
    sb_dir = tmp_path / "sb"
    sb_dir.mkdir()
    out = tmp_path / "out"
    out.mkdir()

    s = act.BrowserSession.__new__(act.BrowserSession)
    s.download_dir = str(out)
    s._sb_downloads_dir = str(sb_dir)
    s.get = lambda url, wait_selector=None: None

    def click(selector):
        (sb_dir / "T-specification.zip.crdownload").write_bytes(b"1")

        def finish():
            (sb_dir / "T-specification.zip.crdownload").unlink()
            (sb_dir / "T-specification.zip").write_bytes(b"zip")

        _later(0.5, finish)

    s.sb = type("SB", (), {"click": staticmethod(click)})()
    path = s.download_via_form("https://example/x", [])
    assert os.path.basename(path) == "T-specification.zip"
    assert os.path.exists(path)


def test_act_download_times_out_when_nothing_starts(tmp_path, monkeypatch):
    from error_scrapers.tenders_act import browser as act
    monkeypatch.setattr(act, "DOWNLOAD_WAIT_SECONDS", 0.2)
    sb_dir = tmp_path / "sb"
    sb_dir.mkdir()
    s = act.BrowserSession.__new__(act.BrowserSession)
    s.download_dir = str(tmp_path)
    s._sb_downloads_dir = str(sb_dir)
    s.get = lambda url, wait_selector=None: None
    s.sb = type("SB", (), {"click": staticmethod(lambda sel: None)})()
    with pytest.raises(TimeoutError, match="did not complete"):
        s.download_via_form("https://example/x", [])


def test_vic_download_uses_the_shared_wait(tmp_path, monkeypatch):
    from error_scrapers.vic_buyingfor import scraper as vic
    monkeypatch.setattr(vic, "DOWNLOAD_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(vic, "DOWNLOAD_STALL_SECONDS", 5)
    monkeypatch.setattr(vic, "DOWNLOAD_HARD_MAX_SECONDS", 10)
    downloads = tmp_path / "dl"
    downloads.mkdir()
    folder = tmp_path / "folder"
    folder.mkdir()
    cls = [v for v in vars(vic).values()
           if isinstance(v, type) and "download_documents" in vars(v)][0]
    s = cls.__new__(cls)
    s._downloads_dir = str(downloads)
    s.get = lambda *a, **k: "<html></html>"
    monkeypatch.setattr(vic, "parse_download_form", lambda html: {"count": 1, "unchecked": False})

    def click(selector):
        (downloads / "x.crdownload").write_bytes(b"1")

        def finish():
            (downloads / "x.crdownload").unlink()
            (downloads / "x.zip").write_bytes(b"zip")

        _later(0.5, finish)

    s.sb = type("SB", (), {"click": staticmethod(click)})()
    path = s.download_documents("https://example/d", str(folder))
    assert os.path.basename(path) == "x.zip"


# ---------------------------------------------------------------------------
# A PDF that is empty at the source is stored without text, not failed
# ---------------------------------------------------------------------------

def test_an_empty_pdf_is_stored_without_text(tmp_path):
    f = tmp_path / "EAN013.pdf"
    f.write_bytes(b"")
    assert common.extract_with_fallback(common.extract_pdf, str(f)) == ""


def test_an_empty_pdf_is_not_a_failed_attachment(tmp_path):
    f = tmp_path / "EAN013.pdf"
    f.write_bytes(b"")
    assert common.extract_attachment_text(str(tmp_path), "EAN013.pdf") is True
    assert (tmp_path / "EAN013.pdf.txt").exists() or any(p.suffix == ".txt" for p in tmp_path.iterdir())


def test_a_different_pdf_error_still_fails(tmp_path):
    f = tmp_path / "bad.pdf"
    f.write_bytes(b"this is not a pdf at all" * 20)
    with pytest.raises(common.ExtractionError) as info:
        common.extract_with_fallback(common.extract_pdf, str(f))
    assert not isinstance(info.value, common.EmptySourceFileError)
