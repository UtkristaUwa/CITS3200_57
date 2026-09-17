"""
Tests for attachment_store: what gets uploaded, what gets skipped, and what
the manager is handed back for the database write.

No GCP credentials are needed -- a fake bucket stands in for the real one.
"""

import os

import pytest
from google.api_core import exceptions as api_exceptions

import attachment_store


class FakeBlob:
    def __init__(self, bucket, path):
        self.bucket = bucket
        self.path = path
        self.content_disposition = None
        self.metadata = None

    def upload_from_filename(self, local_path, content_type=None, if_generation_match=None):
        if if_generation_match == 0 and self.path in self.bucket.objects:
            raise api_exceptions.PreconditionFailed("object already exists")
        with open(local_path, "rb") as f:
            self.bucket.objects[self.path] = {
                "bytes": f.read(),
                "content_type": content_type,
                "content_disposition": self.content_disposition,
                "metadata": self.metadata,
            }


class FakeBucket:
    def __init__(self):
        self.objects = {}

    def blob(self, path):
        return FakeBlob(self, path)


@pytest.fixture
def bucket():
    return FakeBucket()


def write(folder, name, content=b"pretend this is a pdf"):
    path = os.path.join(str(folder), name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def test_uploads_file_and_returns_a_database_record(tmp_path, bucket):
    write(tmp_path, "Statement of Requirements.pdf")

    records = attachment_store.upload_tender_attachments(
        str(tmp_path), source_id="grantconnect", tender_ref="GO8232", bucket=bucket
    )

    assert len(records) == 1
    record = records[0]
    assert record["file_name"] == "Statement of Requirements.pdf"
    assert record["file_type"] == "pdf"
    assert record["content_type"] == "application/pdf"
    assert record["size_bytes"] == len(b"pretend this is a pdf")
    assert len(record["checksum_sha256"]) == 64
    assert record["storage_uri"].startswith("gs://")
    assert "tenders/grantconnect/GO8232/" in record["storage_uri"]


def test_local_copy_is_deleted_so_memory_is_freed(tmp_path, bucket):
    path = write(tmp_path, "big.pdf")

    attachment_store.upload_tender_attachments(
        str(tmp_path), source_id="grantconnect", tender_ref="GO8232", bucket=bucket
    )

    assert not os.path.exists(path)


def test_original_filename_survives_for_the_download_button(tmp_path, bucket):
    write(tmp_path, "Conditions of Contract.pdf")

    attachment_store.upload_tender_attachments(
        str(tmp_path), source_id="austender", tender_ref="26-0084", bucket=bucket
    )

    stored = next(iter(bucket.objects.values()))
    assert stored["content_disposition"] == 'attachment; filename="Conditions of Contract.pdf"'
    assert stored["metadata"]["original_file_name"] == "Conditions of Contract.pdf"
    assert stored["metadata"]["source_id"] == "austender"


def test_rescraping_the_same_file_does_not_overwrite_it(tmp_path, bucket):
    write(tmp_path, "notice.pdf", b"same bytes every run")
    first = attachment_store.upload_tender_attachments(
        str(tmp_path), source_id="austender", tender_ref="26-0084", bucket=bucket
    )

    # Same file, next day's run.
    write(tmp_path, "notice.pdf", b"same bytes every run")
    second = attachment_store.upload_tender_attachments(
        str(tmp_path), source_id="austender", tender_ref="26-0084", bucket=bucket
    )

    assert first[0]["storage_uri"] == second[0]["storage_uri"]
    assert len(bucket.objects) == 1


def test_oversized_attachment_is_skipped(tmp_path, bucket, monkeypatch):
    monkeypatch.setattr(attachment_store, "MAX_ATTACHMENT_MB", 0)
    write(tmp_path, "feature-film.mp4", b"x" * 2048)

    records = attachment_store.upload_tender_attachments(
        str(tmp_path), source_id="austender", tender_ref="26-0084", bucket=bucket
    )

    assert records == []
    assert bucket.objects == {}


def test_extracted_text_is_not_uploaded_as_an_attachment(tmp_path, bucket):
    write(tmp_path, "Requirements.pdf")
    write(tmp_path, "Requirements.pdf.txt", b"extracted text")
    write(tmp_path, "GO8232.txt", b"page text")

    records = attachment_store.upload_tender_attachments(
        str(tmp_path), source_id="grantconnect", tender_ref="GO8232", bucket=bucket
    )

    assert [r["file_name"] for r in records] == ["Requirements.pdf"]


def test_manifest_is_believed_over_guessing_from_the_folder(tmp_path, bucket):
    # A genuine .txt attachment from the portal. The directory-scan fallback
    # would miss it; the scraper's manifest says it is real.
    write(tmp_path, "Addendum 1.txt", b"a real attachment")
    write(tmp_path, "GO8232.txt", b"page text")

    records = attachment_store.upload_tender_attachments(
        str(tmp_path),
        source_id="grantconnect",
        tender_ref="GO8232",
        attachments=[{"file_name": "Addendum 1.txt", "content_type": "text/plain"}],
        bucket=bucket,
    )

    assert [r["file_name"] for r in records] == ["Addendum 1.txt"]
    assert records[0]["content_type"] == "text/plain"
    assert records[0]["file_type"] == "other"


def test_a_missing_file_does_not_stop_the_rest(tmp_path, bucket):
    write(tmp_path, "present.pdf")

    records = attachment_store.upload_tender_attachments(
        str(tmp_path),
        source_id="grantconnect",
        tender_ref="GO8232",
        attachments=[
            {"file_name": "vanished.pdf"},
            {"file_name": "present.pdf"},
        ],
        bucket=bucket,
    )

    assert [r["file_name"] for r in records] == ["present.pdf"]


def test_failed_upload_keeps_the_local_file(tmp_path, monkeypatch, bucket):
    """
    A file we failed to store is the only copy we have. Deleting it here
    would lose it silently -- there is no record of it anywhere else.
    """
    path = write(tmp_path, "notice.pdf")

    def exploding_upload(*args, **kwargs):
        raise RuntimeError("bucket unreachable")

    monkeypatch.setattr(FakeBlob, "upload_from_filename", exploding_upload)

    records = attachment_store.upload_tender_attachments(
        str(tmp_path), source_id="austender", tender_ref="26-0084", bucket=bucket
    )

    assert records == []
    assert os.path.exists(path)
