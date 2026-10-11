"""
A password-protected file inside a document package.

Seen on buy.nsw (10 Oct): a package with one locked PDF failed as a whole
("File ... is encrypted, password required for extraction") and the tender kept
none of its other files. Now the locked file is dropped, logged and reported for
the tender, and the rest of the package is kept. No network.
"""

import io
import zipfile

import pytest

from error_scrapers import common, run_notes
from error_scrapers.buy_nsw import scraper as nsw


@pytest.fixture(autouse=True)
def fresh_notes():
    run_notes.reset()
    yield
    run_notes.reset()


def _lock(data: bytes, name: str) -> bytes:
    """
    Set the 'encrypted' flag on one member of a finished zip, in both its local
    header and its central-directory entry, as a real password-protected entry
    has it. (zipfile clears the flag when writing, so it is patched afterwards.)
    """
    raw = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        offset = zf.getinfo(name).header_offset
    raw[offset + 6] |= 0x1
    encoded = name.encode()
    at = 0
    while (at := raw.find(b"PK\x01\x02", at)) != -1:
        name_len = int.from_bytes(raw[at + 28:at + 30], "little")
        if bytes(raw[at + 46:at + 46 + name_len]) == encoded:
            raw[at + 8] |= 0x1
            break
        at += 4
    return bytes(raw)


def _package(*, locked_name="Annexures/Annesure B.pdf"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Scope.txt", "scope of services")
        zf.writestr(locked_name, b"secret")
        zf.writestr("Price.txt", "prices")
    return io.BytesIO(_lock(buffer.getvalue(), locked_name))


def test_unpack_zip_skips_a_locked_member_and_keeps_the_rest(tmp_path):
    attachments, any_failed = common.unpack_zip(_package(), str(tmp_path))

    assert any_failed is True
    assert sorted(a["file_name"] for a in attachments) == ["Price.txt", "Scope.txt"]
    assert not (tmp_path / "Annesure B.pdf").exists()
    run_notes.note_tender("T1", common.TENDER_PARTIAL)
    assert run_notes.snapshot().partials["T1"] == [(run_notes.LOCKED, "Annesure B.pdf")]


def test_buy_nsw_package_with_a_locked_member_keeps_the_rest(tmp_path):
    attachments = []
    _text, any_failed = nsw._extract_zip_entries(
        _package().getvalue(), str(tmp_path), "OP1", attachments)

    assert any_failed is True
    assert sorted(a["file_name"] for a in attachments) == ["Price.txt", "Scope.txt"]
    run_notes.note_tender("OP1", common.TENDER_PARTIAL)
    assert run_notes.snapshot().partials["OP1"] == [(run_notes.LOCKED, "Annesure B.pdf")]


def test_a_locked_nested_zip_is_skipped_not_opened(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("templates.zip", b"not really a zip")
        zf.writestr("ok.txt", "fine")
    attachments, any_failed = common.unpack_zip(
        io.BytesIO(_lock(buffer.getvalue(), "templates.zip")), str(tmp_path))
    assert any_failed is True
    assert [a["file_name"] for a in attachments] == ["ok.txt"]


def test_buy_nsw_drops_a_file_that_extracts_as_locked(tmp_path, monkeypatch):
    def locked(path):
        raise common.EncryptedFileError("password protected")

    monkeypatch.setattr(common, "extract_pdf", locked)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Locked.pdf", b"%PDF")
        zf.writestr("Open.txt", "hello")
    # the PDF extractor is looked up when the function runs, so patch the table it builds from
    attachments = []
    _text, any_failed = nsw._extract_zip_entries(
        buffer.getvalue(), str(tmp_path), "OP2", attachments)
    assert any_failed is True
    assert [a["file_name"] for a in attachments] == ["Open.txt"]
    assert not (tmp_path / "Locked.pdf").exists()


def test_an_ordinary_package_is_unchanged(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("a.txt", "x")
        zf.writestr("b.txt", "y")
    attachments, any_failed = common.unpack_zip(buffer, str(tmp_path))
    assert any_failed is False and len(attachments) == 2
