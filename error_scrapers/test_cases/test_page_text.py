"""
Tests for the full page text safety net in common.py (visible_page_text and
append_full_page_text), and a check that every scraper uses it.

The scrapers only save the fields they have selectors for; the FULL PAGE TEXT
block at the bottom of each __tender__ txt is what stops a section nobody wrote
a selector for (a site visit, special instructions) from being lost.
"""

import glob
import os

import pytest
from bs4 import BeautifulSoup

from error_scrapers import common

SCRAPERS_DIR = os.path.dirname(os.path.dirname(__file__))


def _text(html, within=None):
    return common.visible_page_text(html, within)


def test_scripts_styles_menus_and_footers_are_dropped():
    html = ("<html><head><title>T</title><style>.a{}</style></head><body>"
            "<nav>Home Tenders</nav><script>var x=1;</script><p>Real content</p>"
            "<noscript>enable js</noscript><footer>Copyright</footer></body></html>")
    assert _text(html) == "Real content"


def test_blocks_and_line_breaks_become_lines():
    html = "<div><h2>Site Visit</h2><p>Bring ID<br>Boots required</p><ul><li>One</li><li>Two</li></ul></div>"
    assert _text(html).splitlines() == ["Site Visit", "Bring ID", "Boots required", "One", "Two"]


def test_inline_tags_stay_on_one_line():
    assert _text("<p>Closes <b>5 Nov</b> at <a href='#'>2pm</a></p>") == "Closes 5 Nov at 2pm"


def test_table_rows_stay_on_one_line():
    html = ("<table><tr><td>Date:</td><td>12 Nov</td></tr>"
            "<tr><td>Item</td><td>Qty</td><td>Price</td></tr></table>")
    assert _text(html).splitlines() == ["Date: 12 Nov", "Item | Qty | Price"]


def test_textarea_html_is_turned_into_text():
    html = "<textarea>&lt;p&gt;First&lt;/p&gt;&lt;p&gt;Second &amp;amp; third&lt;/p&gt;</textarea>"
    assert _text(html).splitlines() == ["First", "Second & third"]


def test_hidden_panels_are_kept():
    assert "Tab two" in _text("<div style='display:none'>Tab two</div><div hidden>x</div>")


def test_repeated_lines_are_collapsed_and_blanks_dropped():
    assert _text("<p>A</p><p>A</p><p> </p><p>B</p>").splitlines() == ["A", "B"]


def test_within_keeps_only_the_content_area():
    html = "<div>Menu</div><main><p>Content</p></main><div>Login</div>"
    assert _text(html, "main") == "Content"


def test_within_falls_back_to_the_whole_page_when_it_matches_nothing():
    assert _text("<div>Menu</div><p>Content</p>", "main").splitlines() == ["Menu", "Content"]


def test_empty_or_missing_html_gives_nothing():
    assert _text("") == ""
    assert _text(None) == ""


def test_very_long_pages_are_cut():
    text = _text("<p>" + "x " * common.FULL_PAGE_MAX_CHARS + "</p>")
    assert text.endswith("[page text cut at the size limit]")
    assert len(text) < common.FULL_PAGE_MAX_CHARS + 100


def test_very_deep_markup_does_not_crash():
    html = "<div>" * 3000 + "deep" + "</div>" * 3000
    assert "deep" in _text(html)


def test_append_adds_the_block_after_the_existing_text(tmp_path):
    folder = str(tmp_path)
    common.save_page_text(folder, "T-1", "FIELDS\n")
    common.append_full_page_text(folder, "T-1", "<main><h2>Site Visit</h2><p>12 Nov</p></main>", "main")
    content = (tmp_path / "__tender__T-1.txt").read_text(encoding="utf-8")
    assert content.startswith("FIELDS\n")
    assert content.index(common.FULL_PAGE_HEADER) > content.index("FIELDS")
    assert content.rstrip().endswith("Site Visit\n12 Nov")


def test_append_creates_the_file_if_it_is_not_there_yet(tmp_path):
    common.append_full_page_text(str(tmp_path), "T-2", "<p>Hello</p>")
    assert "Hello" in (tmp_path / "__tender__T-2.txt").read_text(encoding="utf-8")


def test_append_writes_nothing_for_an_empty_page(tmp_path):
    common.append_full_page_text(str(tmp_path), "T-3", "<script>x</script>")
    assert not (tmp_path / "__tender__T-3.txt").exists()


def test_append_never_raises(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("bad page")
    monkeypatch.setattr(common, "visible_page_text", boom)
    common.save_page_text(str(tmp_path), "T-4", "FIELDS\n")
    common.append_full_page_text(str(tmp_path), "T-4", "<p>x</p>")  # must not raise
    assert (tmp_path / "__tender__T-4.txt").read_text(encoding="utf-8") == "FIELDS\n"


def test_the_source_url_can_still_be_read_back_after_the_block(tmp_path):
    common.save_page_text(str(tmp_path), "T-5", "FIELDS\n")
    common.append_full_page_text(str(tmp_path), "T-5", "<p>Page</p>")
    common.add_source_url(str(tmp_path), "T-5", "https://example.test/t/5")
    assert common.read_source_url(str(tmp_path)) == "https://example.test/t/5"


# ---- the real detail pages ------------------------------------------------

# (scraper folder, detail fixture, a phrase only the page body has)
REAL_PAGES = [
    ("austender", "austender_full_details.html", "ATM Documents Lodgement Page"),
    ("grant_connect", "grantconnect_full_details.html", None),
    ("buy_nsw", "buynsw_full_details.html", None),
    ("vic_buyingfor", "vic_full_details.html", None),
    ("tenders_act", "act_tender_detail.html", None),
    ("nt_qtol", "nt_full_details.html", "Lodgement details"),
    ("tenders_wa", "tenders_wa_details.html", "Issued by Department of Fire and Emergency Services"),
]


def _scraper_module(folder):
    return __import__(f"error_scrapers.{folder}.scraper", fromlist=["scraper"])


def _fixture(folder, name):
    path = os.path.join(SCRAPERS_DIR, folder, "test_cases", name)
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


@pytest.mark.parametrize("folder,name,phrase", REAL_PAGES)
def test_real_page_text_is_not_empty_and_keeps_the_body(folder, name, phrase):
    html = _fixture(folder, name)
    within = getattr(_scraper_module(folder), "PAGE_CONTENT_SELECTOR", None)
    text = _text(html, within)
    assert len(text.splitlines()) > 5
    if phrase:
        assert phrase in text


@pytest.mark.parametrize("folder,name,phrase", REAL_PAGES)
def test_content_selector_matches_the_real_page(folder, name, phrase):
    selector = getattr(_scraper_module(folder), "PAGE_CONTENT_SELECTOR", None)
    if selector is None:
        pytest.skip("this scraper reads the whole page")
    assert BeautifulSoup(_fixture(folder, name), "html.parser").select_one(selector) is not None


def test_austender_page_text_leaves_out_the_logged_in_menu():
    scraper = _scraper_module("austender")
    text = _text(_fixture("austender", "austender_full_details.html"), scraper.PAGE_CONTENT_SELECTOR)
    assert "Last Login" not in text and "Edit Profile" not in text


def test_wa_page_text_leaves_out_the_login_panel():
    scraper = _scraper_module("tenders_wa")
    text = _text(_fixture("tenders_wa", "tenders_wa_details.html"), scraper.PAGE_CONTENT_SELECTOR)
    assert "Forgotten User ID or Password" not in text and "Skip to menu" not in text


def test_every_scraper_adds_the_full_page_text():
    """A new scraper that saves page text but forgets the safety net fails here."""
    missing = []
    for path in glob.glob(os.path.join(SCRAPERS_DIR, "*", "scraper.py")):
        with open(path, encoding="utf-8") as f:
            source = f.read()
        if "save_page_text" in source and "append_full_page_text" not in source:
            missing.append(os.path.basename(os.path.dirname(path)))
    assert not missing, f"scrapers saving page text without the full page text block: {missing}"
