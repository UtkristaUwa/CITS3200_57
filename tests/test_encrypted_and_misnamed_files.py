"""
Two extraction cases seen in the 10 Oct 2026 run.

* A password-protected .xls (NT T25-1527) failed every night. It is dropped,
  logged, and the tender is reported as partial.
* .docx files that are not zip files at all ("Package not found") are stored
  with whatever text they have instead of failing.

No network.
"""

import io
import logging
import os
import zipfile

import pytest

from error_scrapers import common

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MARKER = "EncryptedPackage".encode("utf-16-le")


def _encrypted_xls(path):
    path.write_bytes(OLE + b"\x00" * 64)


def test_encrypted_xls_is_dropped_logged_and_reported(tmp_path, monkeypatch, caplog):
    folder = tmp_path / "T25-1527"
    folder.mkdir()
    _encrypted_xls(folder / "Price.xls")

    def raises_encrypted(path):
        raise common.EncryptedFileError(f"XLS extraction failed on {path}: Workbook is encrypted")

    monkeypatch.setitem(common.EXTRACTORS, ".xls", raises_encrypted)
    with caplog.at_level(logging.INFO, logger="scraper.common"):
        assert common.extract_attachment_text(str(folder), "Price.xls") is False

    assert not (folder / "Price.xls").exists()
    assert not (folder / "Price.xls.txt").exists()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("Price.xls is password protected; dropped" in m and "T25-1527" in m for m in msgs)


def test_extract_xls_raises_the_encrypted_error(tmp_path, monkeypatch):
    import xlrd

    def boom(*a, **k):
        raise xlrd.biffh.XLRDError("Workbook is encrypted")

    monkeypatch.setattr(xlrd, "open_workbook", boom)
    with pytest.raises(common.EncryptedFileError):
        common.extract_xls(str(tmp_path / "x.xls"))


def test_encrypted_error_survives_the_extraction_process(tmp_path, monkeypatch):
    (tmp_path / "Price.xls").write_bytes(b"x")
    monkeypatch.setitem(common.EXTRACTORS, ".xls", _raise_encrypted)
    with pytest.raises(common.EncryptedFileError):
        common.extract_with_fallback(_raise_encrypted, str(tmp_path / "Price.xls"), timeout=10)


def _raise_encrypted(path):
    raise common.EncryptedFileError("Workbook is encrypted")


def test_encrypted_xlsx_is_recognised_from_its_bytes(tmp_path):
    path = tmp_path / "locked.xlsx"
    path.write_bytes(OLE + b"\x00" * 32 + MARKER + b"\x00" * 32)
    with pytest.raises(common.EncryptedFileError):
        common.extract_with_fallback(common.extract_xlsx, str(path), timeout=10)


def test_dropped_file_is_left_out_of_a_zip_package(tmp_path, monkeypatch):
    monkeypatch.setitem(common.EXTRACTORS, ".xls", _raise_encrypted)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("locked.xls", OLE + b"\x00" * 16)
        zf.writestr("note.txt", "hello")
    attachments, any_failed = common.unpack_zip(buffer, str(tmp_path))
    assert any_failed is True
    assert [a["file_name"] for a in attachments] == ["note.txt"]
    assert not (tmp_path / "locked.xls").exists()


def test_dropped_file_raises_from_a_direct_download(tmp_path, monkeypatch):
    monkeypatch.setitem(common.EXTRACTORS, ".xls", _raise_encrypted)

    class Response:
        headers = {"content-type": "application/vnd.ms-excel"}

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield OLE + b"\x00" * 16

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Client:
        def stream(self, *a, **k):
            return Response()

    monkeypatch.setattr(common, "save_attachment_stream",
                        lambda folder, name, response: _save(folder, name))
    with pytest.raises(common.EncryptedFileError):
        common.download_attachment(Client(), "https://x/y.xls", str(tmp_path), "y.xls")
    assert not (tmp_path / "y.xls").exists()


def _save(folder, name):
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(OLE + b"\x00" * 16)
    return path


# --- misnamed .docx ---------------------------------------------------------

def test_html_saved_as_docx_keeps_its_text(tmp_path, caplog):
    path = tmp_path / "Tender-Schedules.docx"
    path.write_text("<html><body><p>Schedule 1</p><p>Price  $5</p></body></html>")
    with caplog.at_level(logging.WARNING, logger="scraper.common"):
        text = common.extract_with_fallback(common.extract_docx, str(path), timeout=10)
    assert "Schedule 1" in text and "$5" in text and "<" not in text
    assert any("is a web/XML page" in r.getMessage() for r in caplog.records)


def test_old_format_word_file_saved_as_docx_is_stored_without_text(tmp_path, caplog):
    path = tmp_path / "old.docx"
    path.write_bytes(OLE + b"\x00" * 100)
    with caplog.at_level(logging.WARNING, logger="scraper.common"):
        assert common.extract_with_fallback(common.extract_docx, str(path), timeout=10) == ""
    assert any("old-format Office file" in r.getMessage() for r in caplog.records)


def test_misnamed_docx_is_not_a_failure(tmp_path):
    (tmp_path / "bad.docx").write_text("<html>hi</html>")
    assert common.extract_attachment_text(str(tmp_path), "bad.docx") is True
    assert (tmp_path / "bad.docx").exists()


def test_a_real_docx_still_extracts(tmp_path):
    import docx
    doc = docx.Document()
    doc.add_paragraph("real words")
    path = tmp_path / "ok.docx"
    doc.save(str(path))
    assert "real words" in common.extract_with_fallback(common.extract_docx, str(path), timeout=30)


def test_garbage_xlsx_still_fails_as_before(tmp_path):
    (tmp_path / "junk.xlsx").write_bytes(b"not a zip")
    assert common.extract_attachment_text(str(tmp_path), "junk.xlsx") is False
