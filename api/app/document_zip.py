"""Stream a tender's stored attachments to the caller as one zip archive.

The archive is built while it is being sent rather than in memory: a single
tender can hold several hundred MB of attachments and the API runs in 512 MiB.
ZipFile writes into an unseekable sink (so it emits data descriptors instead of
seeking back to patch headers), each attachment is read from GCS in chunks, and
whatever the sink has collected is yielded after every chunk. Peak memory is
roughly one chunk plus zlib's window, whatever the tender's size.
"""

import io
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

ALLOWED_BUCKET_PREFIX = "tenderai-"

# Large enough that GCS round trips don't dominate, small enough to keep the
# per-request memory flat.
CHUNK_SIZE = 1024 * 1024

# Most tender attachments (PDF, DOCX, XLSX, ZIP) are already compressed, so a
# higher level costs CPU on the API's single vCPU for almost no size gain.
COMPRESS_LEVEL = 1

# ZIP timestamps cannot represent anything before 1980.
_EPOCH = (1980, 1, 1, 0, 0, 0)

# attachment_store prefixes every object with a 16-hex-char content hash:
# "f239d7a8b595541c-Request for Tender.pdf".
_HASH_PREFIX = re.compile(r"^[0-9a-f]{16}-", re.IGNORECASE)


@dataclass
class ArchiveEntry:
    name: str
    blob: object  # google.cloud.storage.Blob, already reloaded (size, updated)


def parse_storage_uri(uri: object) -> tuple[str, str] | None:
    """`gs://bucket/path` → (bucket, path), or None if it isn't one of ours."""
    if not isinstance(uri, str) or not uri.startswith("gs://"):
        return None
    bucket, _, path = uri[len("gs://"):].partition("/")
    if not bucket.startswith(ALLOWED_BUCKET_PREFIX) or not path:
        return None
    return bucket, path


def entry_name(document: dict, object_path: str) -> str:
    """The filename a person should see inside the archive.

    Prefers the document's own file_name and falls back to the object's
    basename without its hash prefix. Only the final path component is kept,
    so nothing in the data can place a file outside the extraction folder.
    """
    raw = document.get("file_name") or _HASH_PREFIX.sub("", PurePosixPath(object_path).name)
    name = PurePosixPath(str(raw).replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f]", "", name)
    return name if name not in ("", ".", "..") else "attachment"


def dedupe_names(names: Iterable[str]) -> list[str]:
    """Make names unique (case-insensitively): "a.pdf", "a (2).pdf", ..."""
    seen: set[str] = set()
    result = []
    for name in names:
        candidate = name
        stem, dot, ext = name.rpartition(".")
        if not dot or not stem:
            stem, ext = name, ""
        n = 2
        while candidate.lower() in seen:
            candidate = f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})"
            n += 1
        seen.add(candidate.lower())
        result.append(candidate)
    return result


def archive_filename(tender: dict) -> str:
    """ASCII-safe download name, e.g. "FIN-2025-26-00788-documents.zip"."""
    base = tender.get("source_reference_id") or tender.get("tender_id") or "tender"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(base)).strip("._") or "tender"
    return f"{safe[:100]}-documents.zip"


class _Sink(io.RawIOBase):
    """Collects what ZipFile writes until the generator hands it on.

    It deliberately doesn't support tell/seek: that is what makes ZipFile
    stream (data descriptors) instead of seeking back to rewrite headers.
    """

    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        self._parts.append(bytes(b))
        return len(b)

    def drain(self) -> bytes:
        data = b"".join(self._parts)
        self._parts.clear()
        return data


def _timestamp(blob) -> tuple[int, int, int, int, int, int]:
    updated = getattr(blob, "updated", None)
    if updated is None or updated.year < 1980:
        return _EPOCH
    return updated.timetuple()[:6]


def stream_zip(entries: list[ArchiveEntry], chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=COMPRESS_LEVEL) as zf:
        for entry in entries:
            info = zipfile.ZipInfo(entry.name, date_time=_timestamp(entry.blob))
            info.compress_type = zipfile.ZIP_DEFLATED
            # Known up front so ZipFile picks ZIP64 headers for any file that
            # needs them; it can't go back and change its mind on a stream.
            info.file_size = entry.blob.size or 0
            with zf.open(info, "w") as dest, entry.blob.open("rb", chunk_size=chunk_size) as src:
                while chunk := src.read(chunk_size):
                    dest.write(chunk)
                    if data := sink.drain():
                        yield data
            if data := sink.drain():
                yield data
    # Closing the ZipFile writes the central directory.
    if data := sink.drain():
        yield data
