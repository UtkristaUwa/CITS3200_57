from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import types
import zipfile

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "api"
sys.path.insert(0, str(API_ROOT))
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")


# Same Firebase stand-in as test_admin_config.py: just enough import surface to
# build the app; authentication itself is replaced by a dependency override.
if "firebase_admin" not in sys.modules:
    firebase_admin = types.ModuleType("firebase_admin")
    firebase_admin._apps = [object()]
    firebase_auth = types.ModuleType("firebase_admin.auth")
    firebase_credentials = types.ModuleType("firebase_admin.credentials")
    firebase_firestore = types.ModuleType("firebase_admin.firestore")
    firebase_firestore.client = lambda: None
    firebase_firestore.SERVER_TIMESTAMP = object()
    firebase_admin.auth = firebase_auth
    firebase_admin.credentials = firebase_credentials
    firebase_admin.firestore = firebase_firestore
    sys.modules["firebase_admin"] = firebase_admin
    sys.modules["firebase_admin.auth"] = firebase_auth
    sys.modules["firebase_admin.credentials"] = firebase_credentials
    sys.modules["firebase_admin.firestore"] = firebase_firestore

from app import document_zip
from app.auth import current_user
from app.config import settings
from app.main import app
from app.routers import tenders as tenders_router


BUCKET = "tenderai-dev-documents"
PREFIX = "tenders/austender/FIN-1"


class FakeBlob:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self.data = data
        self.size = len(data)
        self.updated = datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)

    def open(self, mode: str, chunk_size: int | None = None):
        assert mode == "rb"
        return io.BytesIO(self.data)


class FailingReader(io.BytesIO):
    """Hands out the first chunk, then fails the way a dropped GCS read would."""

    def __init__(self, data: bytes):
        super().__init__(data)
        self.reads = 0

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        if self.reads > 1:
            raise ConnectionResetError("connection reset by GCS")
        return super().read(size)


class BreaksMidRead(FakeBlob):
    def open(self, mode: str, chunk_size: int | None = None):
        return FailingReader(self.data)


class FakeStorage:
    def __init__(self, buckets: dict[str, dict[str, FakeBlob]], list_error: Exception | None = None):
        self.buckets = buckets
        self.list_error = list_error
        self.listings: list[tuple[str, str | None]] = []

    def list_blobs(self, bucket: str, prefix: str | None = None):
        self.listings.append((bucket, prefix))
        if self.list_error:
            raise self.list_error
        return [b for name, b in self.buckets.get(bucket, {}).items() if name.startswith(prefix or "")]


class StorageFactory:
    """Stands in for get_storage_client() and counts how often it was asked."""

    def __init__(self, storage: FakeStorage | None = None, error: Exception | None = None):
        self.storage = storage
        self.error = error
        self.calls = 0

    def __call__(self) -> FakeStorage:
        self.calls += 1
        if self.error:
            raise self.error
        return self.storage


def doc(file_name: str, object_name: str | None, bucket: str = BUCKET) -> dict:
    uri = f"gs://{bucket}/{PREFIX}/{object_name}" if object_name else None
    return {"file_name": file_name, "file_type": "pdf", "storage_uri": uri}


def no_cloud(*_args, **_kwargs):
    raise AssertionError("a test reached a real Google Cloud client")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No test may build a real BigQuery or Cloud Storage client."""
    monkeypatch.setattr(tenders_router, "get_client", no_cloud)
    monkeypatch.setattr(tenders_router, "get_storage_client", no_cloud)


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[current_user] = lambda: {"uid": "user-1", "isAdmin": False}
    monkeypatch.setattr(settings, "use_mock_data", False)
    monkeypatch.setattr(tenders_router, "get_client", lambda: object())
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def serve(monkeypatch, tender: dict | None, blobs: dict[str, bytes | FakeBlob], **storage_kwargs) -> StorageFactory:
    monkeypatch.setattr(tenders_router, "get_tender_documents", lambda _client, _id: tender)
    stored = {
        f"{PREFIX}/{name}": data if isinstance(data, FakeBlob) else FakeBlob(f"{PREFIX}/{name}", data)
        for name, data in blobs.items()
    }
    factory = StorageFactory(FakeStorage({BUCKET: stored}, **storage_kwargs))
    monkeypatch.setattr(tenders_router, "get_storage_client", factory)
    return factory


def open_zip(response) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None
    return archive


def test_zips_every_stored_document_under_its_own_name(client, monkeypatch):
    tender = {
        "tender_id": "t-1",
        "source_reference_id": "FIN-2025-26-00788",
        "documents": [
            doc("Conditions of Tender.docx", "02bfb1538b6abefd-Conditions of Tender.docx"),
            doc("Pricing.xlsx", "d7181d7f5c8ee169-Pricing.xlsx"),
        ],
    }
    serve(monkeypatch, tender, {
        "02bfb1538b6abefd-Conditions of Tender.docx": b"docx bytes",
        "d7181d7f5c8ee169-Pricing.xlsx": b"xlsx bytes" * 1000,
    })

    response = client.get("/tenders/t-1/documents/zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="FIN-2025-26-00788-documents.zip"'
    archive = open_zip(response)
    assert archive.namelist() == ["Conditions of Tender.docx", "Pricing.xlsx"]
    assert archive.read("Pricing.xlsx") == b"xlsx bytes" * 1000
    assert archive.getinfo("Pricing.xlsx").date_time == (2026, 9, 1, 10, 30, 0)


def test_skips_pipeline_files_foreign_buckets_and_repeats(client, monkeypatch):
    tender = {
        "tender_id": "t-1",
        "source_reference_id": None,
        "documents": [
            doc("Scraped page.txt", None),
            doc("Elsewhere.pdf", "aaaaaaaaaaaaaaaa-Elsewhere.pdf", bucket="someone-elses-bucket"),
            doc("RFT.pdf", "cccccccccccccccc-RFT.pdf"),
            doc("RFT.pdf", "cccccccccccccccc-RFT.pdf"),
        ],
    }
    factory = serve(monkeypatch, tender, {"cccccccccccccccc-RFT.pdf": b"rft"})

    response = client.get("/tenders/t-1/documents/zip")

    assert response.status_code == 200
    # The foreign bucket is never touched; the tender's folder is listed once.
    assert factory.storage.listings == [(BUCKET, f"{PREFIX}/")]
    assert response.headers["content-disposition"] == 'attachment; filename="t-1-documents.zip"'
    assert open_zip(response).namelist() == ["RFT.pdf"]


def test_two_different_files_with_the_same_name_both_survive(client, monkeypatch):
    tender = {
        "tender_id": "t-1",
        "documents": [
            doc("Addendum.pdf", "1111111111111111-Addendum.pdf"),
            doc("addendum.pdf", "2222222222222222-addendum.pdf"),
        ],
    }
    serve(monkeypatch, tender, {
        "1111111111111111-Addendum.pdf": b"first",
        "2222222222222222-addendum.pdf": b"second",
    })

    archive = open_zip(client.get("/tenders/t-1/documents/zip"))

    assert archive.namelist() == ["Addendum.pdf", "addendum (2).pdf"]
    assert archive.read("addendum (2).pdf") == b"second"


def test_unknown_tender_is_404(client, monkeypatch):
    serve(monkeypatch, None, {})
    response = client.get("/tenders/nope/documents/zip")
    assert response.status_code == 404
    assert response.json()["detail"] == "Tender not found"


@pytest.mark.parametrize(
    "documents",
    [[], None, [doc("Scraped page.txt", None)], [doc("Elsewhere.pdf", "x-Elsewhere.pdf", bucket="other")]],
    ids=["empty-list", "null", "pipeline-file-only", "foreign-bucket-only"],
)
def test_no_attachments_is_404_before_any_storage_client(client, monkeypatch, documents):
    factory = serve(monkeypatch, {"tender_id": "t-1", "documents": documents}, {})

    response = client.get("/tenders/t-1/documents/zip")

    assert response.status_code == 404
    assert response.json()["detail"] == "No stored documents for this tender"
    assert factory.calls == 0


def test_missing_object_fails_before_streaming_and_names_the_file(client, monkeypatch, caplog):
    tender = {
        "tender_id": "t-1",
        "documents": [
            doc("RFT.pdf", "cccccccccccccccc-RFT.pdf"),
            doc("Addendum 3.pdf", "bbbbbbbbbbbbbbbb-Addendum 3.pdf"),
        ],
    }
    serve(monkeypatch, tender, {"cccccccccccccccc-RFT.pdf": b"rft"})

    response = client.get("/tenders/t-1/documents/zip")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json()["detail"] == "Missing from document storage: Addendum 3.pdf"
    assert "t-1" in caplog.text and "Addendum 3.pdf" in caplog.text


def test_many_missing_objects_are_summarised(client, monkeypatch):
    documents = [doc(f"File {i}.pdf", f"{i:016x}-File {i}.pdf") for i in range(7)]
    serve(monkeypatch, {"tender_id": "t-1", "documents": documents}, {})

    detail = client.get("/tenders/t-1/documents/zip").json()["detail"]

    assert detail == "Missing from document storage: File 0.pdf, File 1.pdf, File 2.pdf, File 3.pdf, File 4.pdf and 2 more"


@pytest.mark.parametrize(
    "storage_kwargs",
    [
        {"error": RuntimeError("no default credentials")},
        {"list_error": PermissionError("403 storage.objects.list denied")},
    ],
    ids=["client-init-fails", "listing-fails"],
)
def test_storage_unavailable_is_503_not_a_partial_zip(client, monkeypatch, caplog, storage_kwargs):
    tender = {"tender_id": "t-1", "documents": [doc("RFT.pdf", "cccccccccccccccc-RFT.pdf")]}
    monkeypatch.setattr(tenders_router, "get_tender_documents", lambda _client, _id: tender)
    error = storage_kwargs.get("error")
    storage = None if error else FakeStorage({}, list_error=storage_kwargs["list_error"])
    monkeypatch.setattr(tenders_router, "get_storage_client", StorageFactory(storage, error=error))

    response = client.get("/tenders/t-1/documents/zip")

    assert response.status_code == 503
    assert response.json()["detail"] == "Document storage is unavailable right now. Please try again."
    assert "tender t-1" in caplog.text


def test_read_failure_mid_stream_aborts_instead_of_finishing_the_zip(client, monkeypatch, caplog):
    tender = {
        "tender_id": "t-1",
        "documents": [
            doc("RFT.pdf", "cccccccccccccccc-RFT.pdf"),
            doc("Drawings.zip", "dddddddddddddddd-Drawings.zip"),
        ],
    }
    serve(monkeypatch, tender, {
        "cccccccccccccccc-RFT.pdf": b"rft",
        "dddddddddddddddd-Drawings.zip": BreaksMidRead(f"{PREFIX}/dddddddddddddddd-Drawings.zip", os.urandom(4 * 1024 * 1024)),
    })

    # The 200 has gone out by the time the read fails, so the failure surfaces
    # as an exception (the server drops the connection), not a status code.
    with pytest.raises(document_zip.ArchiveStreamError, match="Drawings.zip"):
        client.get("/tenders/t-1/documents/zip")

    assert "Aborting zip for tender t-1" in caplog.text
    assert "Drawings.zip" in caplog.text


def test_malformed_tender_id_is_rejected(client, monkeypatch):
    serve(monkeypatch, None, {})
    assert client.get("/tenders/bad id!/documents/zip").status_code == 422


def test_mock_mode_has_no_stored_documents(client, monkeypatch):
    # Runs with no Google credentials at all: the offline fixture makes any
    # attempt to build a Cloud Storage client fail the test.
    monkeypatch.setattr(settings, "use_mock_data", True)
    response = client.get("/tenders/mock-1/documents/zip")
    assert response.status_code == 404
    assert response.json()["detail"] == "No stored documents for this tender"


def test_requires_authentication():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        response = test_client.get("/tenders/t-1/documents/zip")
    assert response.status_code == 401


def test_archive_is_streamed_in_pieces_not_built_in_memory():
    data = os.urandom(512 * 1024)
    entries = [document_zip.ArchiveEntry("big.pdf", FakeBlob("x/big.pdf", data))]

    pieces = list(document_zip.stream_zip(entries, chunk_size=16 * 1024))

    assert len(pieces) > 4
    assert max(len(p) for p in pieces) < len(data) / 4
    assert zipfile.ZipFile(io.BytesIO(b"".join(pieces))).read("big.pdf") == data


def test_stream_that_fails_partway_never_yields_a_valid_zip():
    entries = [
        document_zip.ArchiveEntry("first.pdf", FakeBlob("x/first.pdf", b"first")),
        document_zip.ArchiveEntry("big.pdf", BreaksMidRead("x/big.pdf", os.urandom(256 * 1024))),
    ]
    sent = []

    with pytest.raises(document_zip.ArchiveStreamError, match="big.pdf"):
        for piece in document_zip.stream_zip(entries, chunk_size=16 * 1024, tender_id="t-9"):
            sent.append(piece)

    assert sent, "some bytes went out before the failure"
    # No central directory was sent, so what the client holds can't be opened.
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(b"".join(sent)))


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\boot.ini", "boot.ini"),
        ("folder/Spec.pdf", "Spec.pdf"),
        ("..", "attachment"),
        ("Tab\there.pdf", "Tabhere.pdf"),
    ],
)
def test_entry_names_cannot_escape_the_extraction_folder(file_name, expected):
    assert document_zip.entry_name({"file_name": file_name}, f"{PREFIX}/x") == expected


def test_entry_name_falls_back_to_the_object_name_without_its_hash():
    assert document_zip.entry_name({}, f"{PREFIX}/f239d7a8b595541c-RFT Part A.pdf") == "RFT Part A.pdf"
