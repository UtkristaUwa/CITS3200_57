"""
Reasons for incomplete tenders that were missing, and the buy.nsw package fix.

* buy.nsw: a package that fails to download keeps the tender's page text, asks
  again after a timeout, and never writes the signed link's query string (an AWS
  key id and signature) to the log.
* tenders_act: a tender that comes out incomplete says why.

No network.
"""

import logging

import httpx
import pytest

from error_scrapers import common, run_notes
from error_scrapers.buy_nsw import scraper as nsw
from error_scrapers.tenders_act import scraper as act

SIGNED = ("https://bucket.s3.amazonaws.com/p/opportunity-package-1.zip"
          "?X-Amz-Credential=AKIAEXAMPLE%2F20261010&X-Amz-Signature=deadbeef")


@pytest.fixture(autouse=True)
def fresh_notes():
    run_notes.reset()
    yield
    run_notes.reset()


class Response:
    def __init__(self, status=200, text="<html></html>", content=b""):
        self.status_code, self.text, self.content = status, text, content
        self.url = "https://buy.nsw.gov.au/x"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Client error '{self.status_code}' for url '{SIGNED}'",
                request=httpx.Request("GET", SIGNED), response=self)


class Client:
    def __init__(self, package):
        self.package, self.calls = package, []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url == SIGNED:
            result = self.package.pop(0) if len(self.package) > 1 else self.package[0]
            if isinstance(result, Exception):
                raise result
            return result
        return Response()


@pytest.fixture
def nsw_page(monkeypatch, tmp_path):
    monkeypatch.setattr(nsw.time, "sleep", lambda s: None)
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    monkeypatch.setattr(nsw, "parse_detail",
                        lambda html: ({"opportunity_id": "OP1", "title": "T"}, common.SITE_SUCCESS))
    monkeypatch.setattr(nsw, "find_package_url", lambda html: SIGNED)
    return tmp_path


def test_a_failed_package_logs_no_signed_query_and_keeps_the_page_text(nsw_page, caplog):
    client = Client([Response(status=403)])
    with caplog.at_level(logging.WARNING):
        code, tender = nsw.scrape_opportunity(client, "https://buy.nsw.gov.au/op1", str(nsw_page))

    assert code == common.TENDER_PARTIAL
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "package download/extract FAILED" in logged and "HTTPStatusError" in logged
    assert "AKIA" not in logged and "X-Amz" not in logged and "deadbeef" not in logged
    assert (nsw_page / "OP1" / "__tender__OP1.txt").exists()  # the page text survived
    run_notes.note_tender("OP1", code)
    assert run_notes.snapshot().partials["OP1"] == [
        (run_notes.DOWNLOAD, "document package, HTTPStatusError")]


def test_a_package_timeout_is_asked_again(nsw_page, monkeypatch):
    zipped = Response(content=b"zip")
    monkeypatch.setattr(nsw, "extract_package",
                        lambda content, folder, oid: {"page_text": "", "attachments": [], "any_failed": False})
    client = Client([httpx.ReadTimeout("slow"), zipped])
    code, _ = nsw.scrape_opportunity(client, "https://buy.nsw.gov.au/op1", str(nsw_page))
    assert code == common.SITE_SUCCESS
    assert client.calls.count(SIGNED) == 2


def test_an_expired_link_is_not_asked_again(nsw_page):
    client = Client([Response(status=403)])
    nsw.scrape_opportunity(client, "https://buy.nsw.gov.au/op1", str(nsw_page))
    assert client.calls.count(SIGNED) == 1


# --- tenders_act -----------------------------------------------------------

class ActSession:
    def __init__(self, form, files=0):
        self.form, self.files = form, files

    def get(self, url):
        return "<html></html>"

    def download_via_form(self, url, ids):
        return "zip"


def test_act_documents_page_without_a_form_is_a_portal_problem(monkeypatch):
    monkeypatch.setattr(act, "parse_download_form",
                        lambda html: {"code": common.SITE_STRUCTURE_CHANGE, "ids": []})
    assert act._scrape_documents(ActSession(None), "u", "f") == ([], 0, False)
    run_notes.note_tender("A1", common.TENDER_PARTIAL)
    assert run_notes.snapshot().partials["A1"] == [(run_notes.PORTAL, None)]


def test_act_fewer_files_than_offered_says_how_many(monkeypatch, tmp_path):
    monkeypatch.setattr(act, "parse_download_form",
                        lambda html: {"code": common.SITE_SUCCESS, "ids": ["1", "2", "3"]})
    monkeypatch.setattr(common, "unpack_zip", lambda path, folder: ([{"file_name": "a.pdf"}], False))
    monkeypatch.setattr(act.os, "remove", lambda p: None)
    _, advertised, ok = act._scrape_documents(ActSession(None), "u", str(tmp_path))
    assert (advertised, ok) == (3, False)
    run_notes.note_tender("A2", common.TENDER_PARTIAL)
    assert run_notes.snapshot().partials["A2"] == [(run_notes.DOWNLOAD, "1 of 3 documents")]


def test_act_complete_package_notes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(act, "parse_download_form",
                        lambda html: {"code": common.SITE_SUCCESS, "ids": ["1"]})
    monkeypatch.setattr(common, "unpack_zip", lambda path, folder: ([{"file_name": "a.pdf"}], False))
    monkeypatch.setattr(act.os, "remove", lambda p: None)
    assert act._scrape_documents(ActSession(None), "u", str(tmp_path))[2] is True
    run_notes.note_tender("A3", common.SITE_SUCCESS)
    assert run_notes.snapshot().partials == {}
