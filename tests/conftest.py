"""
Shared test fixtures.

Two kinds of test live in this suite:

  * Offline tests (the default) run against fixture HTML and a fake tender
    portal served from localhost, so they are fast and deterministic and can
    run in CI with no network and no portal credentials.

  * Live tests, marked `@pytest.mark.live`, hit the real portals. They are
    skipped unless --live is passed (or RUN_LIVE_TESTS=1 is set), because the
    real sites go down, rate-limit, and sit behind Cloudflare.
"""

import os
import sys
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"

sys.path.insert(0, str(REPO_ROOT))


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="also run the tests that hit the real tender portals",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: hits a real tender portal (needs --live and network)"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live") or os.environ.get("RUN_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="needs --live (or RUN_LIVE_TESTS=1)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def fixtures():
    """Directory holding the captured portal HTML."""
    return FIXTURES


def read_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class _QuietHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler without the per-request logging noise."""

    def log_message(self, *args):
        pass


@pytest.fixture
def local_site(tmp_path_factory):
    """
    Serve a directory over real HTTP on localhost.

    Returns (base_url, root_path): write files into root_path, fetch them from
    base_url. Using a real socket rather than a mocked transport means the
    tests exercise the actual streaming download path, redirects and status
    handling, which is the part most likely to break.
    """
    root = tmp_path_factory.mktemp("site")
    handler = partial(_QuietHandler, directory=str(root))
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", root
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def output_dir(tmp_path):
    """A throwaway tenders_data/ for one test."""
    directory = tmp_path / "tenders_data"
    directory.mkdir()
    return directory
