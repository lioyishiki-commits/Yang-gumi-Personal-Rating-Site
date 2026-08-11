"""Read-only NSFW supplement built from Bangumi's official weekly Archive.

The public v0 API intentionally omits some adult subjects.  Yang-gumi keeps a
small local gzip index containing only NSFW anime, books and games so those
public subjects can still be searched and scored without storing a Bangumi
account cookie or shipping adult metadata in the repository.
"""
from __future__ import annotations

import gzip
import json
import re
import struct
import threading
import unicodedata
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import requests


ROOT = Path(__file__).resolve().parent
LATEST_URL = "https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json"
INDEX_PATH = ROOT / "data" / "bangumi_archive_r18.jsonl.gz"
METADATA_PATH = ROOT / "data" / "bangumi_archive_r18_meta.json"
USER_AGENT = "Yang-gumi/1.0 (+local personal rating site)"
TIMEOUT = 30
SUBJECT_TYPES = {1, 2, 4}

_refresh_lock = threading.Lock()
_index_lock = threading.Lock()
_index_mtime_ns = -1
_records_by_id: dict[int, dict[str, Any]] = {}
_records: list[dict[str, Any]] = []


class ArchiveError(RuntimeError):
    pass


def _request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    stream: bool = False,
    timeout: int = TIMEOUT,
) -> requests.Response:
    merged_headers = {"User-Agent": USER_AGENT, "Accept": "application/json,*/*"}
    merged_headers.update(headers or {})
    try:
        response = requests.get(
            url,
            headers=merged_headers,
            timeout=timeout,
            stream=stream,
            allow_redirects=True,
        )
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        raise ArchiveError(f"Bangumi Archive request failed: {exc}") from exc


def latest_archive() -> dict[str, Any]:
    response = _request(LATEST_URL)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ArchiveError("Bangumi Archive latest.json is invalid") from exc
    if not isinstance(payload, dict) or not payload.get("browser_download_url"):
        raise ArchiveError("Bangumi Archive latest.json is incomplete")
    return payload


def _read_metadata() -> dict[str, Any]:
    try:
        payload = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def archive_refresh_due(latest: dict[str, Any] | None = None) -> bool:
    if not INDEX_PATH.exists():
        return True
    metadata = _read_metadata()
    if latest is None:
        try:
            latest = latest_archive()
        except ArchiveError:
            return False
    return str(metadata.get("asset_id") or "") != str(latest.get("id") or "")


def _content_length(url: str) -> int:
    try:
        response = requests.head(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()
        return int(response.headers.get("Content-Length") or 0)
    except (requests.RequestException, TypeError, ValueError) as exc:
        raise ArchiveError(f"Unable to inspect Bangumi Archive asset: {exc}") from exc


def _range_bytes(url: str, start: int, end: int) -> bytes:
    response = _request(
        url,
        headers={"Range": f"bytes={int(start)}-{int(end)}", "Accept": "*/*"},
        timeout=60,
    )
    data = response.content
    expected = int(end) - int(start) + 1
    if len(data) != expected:
        raise ArchiveError(
            f"Bangumi Archive range length mismatch: expected {expected}, got {len(data)}"
        )
    return data


def _subject_zip_entry(url: str, total_size: int) -> dict[str, int]:
    tail_size = min(total_size, 2 * 1024 * 1024)
    tail_start = total_size - tail_size
    tail = _range_bytes(url, tail_start, total_size - 1)
    eocd_pos = tail.rfind(b"PK\x05\x06")
    if eocd_pos < 0 or eocd_pos + 22 > len(tail):
        raise ArchiveError("Bangumi Archive ZIP end record was not found")
    _, _, _, _, _, central_size, central_offset, _ = struct.unpack_from(
        "<4s4H2IH", tail, eocd_pos
    )
    central_end = central_offset + central_size - 1
    if central_offset >= tail_start and central_end < total_size:
        central = tail[central_offset - tail_start:central_end - tail_start + 1]
    else:
        central = _range_bytes(url, central_offset, central_end)

    position = 0
    while position + 46 <= len(central):
        if central[position:position + 4] != b"PK\x01\x02":
            break
        values = struct.unpack_from("<4s6H3I5H2I", central, position)
        method = int(values[4])
        compressed_size = int(values[8])
        uncompressed_size = int(values[9])
        name_len, extra_len, comment_len = map(int, values[10:13])
        local_offset = int(values[-1])
        name_start = position + 46
        name = central[name_start:name_start + name_len].decode("utf-8", "replace")
        if name == "subject.jsonlines":
            local = _range_bytes(url, local_offset, local_offset + 65535)
            if local[:4] != b"PK\x03\x04":
                raise ArchiveError("Bangumi Archive subject ZIP header is invalid")
            local_values = struct.unpack_from("<4s5H3I2H", local, 0)
            local_name_len = int(local_values[-2])
            local_extra_len = int(local_values[-1])
            return {
                "method": method,
                "compressed_size": compressed_size,
                "uncompressed_size": uncompressed_size,
                "data_start": local_offset + 30 + local_name_len + local_extra_len,
            }
        position = name_start + name_len + extra_len + comment_len
    raise ArchiveError("Bangumi Archive subject.jsonlines entry was not found")


def _iter_uncompressed_chunks(
    url: str,
    *,
    start: int,
    compressed_size: int,
    method: int,
) -> Iterator[bytes]:
    if method not in {0, 8}:
        raise ArchiveError(f"Unsupported Bangumi Archive ZIP method: {method}")
    end = start + compressed_size - 1
    response = _request(
        url,
        headers={"Range": f"bytes={start}-{end}", "Accept": "*/*"},
        stream=True,
        timeout=120,
    )
    decompressor = zlib.decompressobj(-zlib.MAX_WBITS) if method == 8 else None
    read_bytes = 0
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        read_bytes += len(chunk)
        output = decompressor.decompress(chunk) if decompressor is not None else chunk
        if output:
            yield output
    if read_bytes != compressed_size:
        raise ArchiveError(
            f"Bangumi Archive subject payload was truncated: {read_bytes}/{compressed_size}"
        )
    if decompressor is not None:
        output = decompressor.flush()
        if output:
            yield output


def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "id", "type", "name", "name_cn", "platform", "summary", "nsfw",
            "date", "tags", "meta_tags", "score", "score_details", "rank", "infobox",
        )
        if record.get(key) not in (None, "", [], {})
    }


def refresh_r18_index(*, force: bool = False) -> dict[str, Any]:
    """Download only the compressed subject member and atomically rebuild the index."""
    with _refresh_lock:
        latest = latest_archive()
        if not force and not archive_refresh_due(latest):
            metadata = _read_metadata()
            return {**metadata, "changed": False}
        url = str(latest["browser_download_url"])
        total_size = _content_length(url)
        if total_size <= 0:
            raise ArchiveError("Bangumi Archive asset size is unavailable")
        entry = _subject_zip_entry(url, total_size)
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = INDEX_PATH.with_suffix(INDEX_PATH.suffix + ".tmp")
        kept = 0
        buffer = b""
        try:
            with gzip.open(temporary, "wt", encoding="utf-8", newline="\n") as output:
                for chunk in _iter_uncompressed_chunks(
                    url,
                    start=entry["data_start"],
                    compressed_size=entry["compressed_size"],
                    method=entry["method"],
                ):
                    buffer += chunk
                    lines = buffer.split(b"\n")
                    buffer = lines.pop()
                    for raw_line in lines:
                        if not raw_line:
                            continue
                        try:
                            record = json.loads(raw_line)
                        except (ValueError, TypeError, json.JSONDecodeError):
                            continue
                        if (
                            isinstance(record, dict)
                            and bool(record.get("nsfw"))
                            and int(record.get("type") or 0) in SUBJECT_TYPES
                        ):
                            output.write(json.dumps(_compact_record(record), ensure_ascii=False))
                            output.write("\n")
                            kept += 1
                if buffer.strip():
                    try:
                        record = json.loads(buffer)
                    except (ValueError, TypeError, json.JSONDecodeError):
                        record = None
                    if (
                        isinstance(record, dict)
                        and bool(record.get("nsfw"))
                        and int(record.get("type") or 0) in SUBJECT_TYPES
                    ):
                        output.write(json.dumps(_compact_record(record), ensure_ascii=False))
                        output.write("\n")
                        kept += 1
            temporary.replace(INDEX_PATH)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
        metadata = {
            "asset_id": latest.get("id"),
            "asset_name": latest.get("name"),
            "asset_digest": latest.get("digest"),
            "asset_updated_at": latest.get("updated_at"),
            "refreshed_at": datetime.now().isoformat(timespec="seconds"),
            "records": kept,
            "subject_compressed_bytes": entry["compressed_size"],
            "source": "Bangumi Archive",
        }
        meta_tmp = METADATA_PATH.with_suffix(".tmp")
        meta_tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_tmp.replace(METADATA_PATH)
        _invalidate_memory_index()
        return {**metadata, "changed": True}


def _invalidate_memory_index() -> None:
    global _index_mtime_ns, _records_by_id, _records
    with _index_lock:
        _index_mtime_ns = -1
        _records_by_id = {}
        _records = []


def _load_index() -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    global _index_mtime_ns, _records_by_id, _records
    try:
        mtime_ns = int(INDEX_PATH.stat().st_mtime_ns)
    except OSError:
        return {}, []
    with _index_lock:
        if _index_mtime_ns == mtime_ns:
            return _records_by_id, _records
        by_id: dict[int, dict[str, Any]] = {}
        rows: list[dict[str, Any]] = []
        try:
            with gzip.open(INDEX_PATH, "rt", encoding="utf-8") as source:
                for line in source:
                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(record, dict) or not str(record.get("id") or "").isdigit():
                        continue
                    subject_id = int(record["id"])
                    by_id[subject_id] = record
                    rows.append(record)
        except OSError:
            return {}, []
        _records_by_id = by_id
        _records = rows
        _index_mtime_ns = mtime_ns
        return _records_by_id, _records


def archive_subject(subject_id: int) -> dict[str, Any] | None:
    by_id, _ = _load_index()
    record = by_id.get(int(subject_id))
    return dict(record) if record else None


def archive_subjects() -> list[dict[str, Any]]:
    """Return the compact NSFW public-subject snapshot without network access."""
    _, rows = _load_index()
    return [dict(record) for record in rows]


def _normalize_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.translate(str.maketrans({"監": "监", "獄": "狱"}))
    normalized = "".join(character for character in text if character.isalnum())
    for roman, digit in (("iii", "3"), ("ii", "2"), ("iv", "4")):
        normalized = re.sub(
            rf"(?<=[a-z0-9]){roman}(?=$|[^\x00-\x7f])",
            digit,
            normalized,
        )
    return normalized


def search_archive_subjects(
    keyword: str,
    *,
    subject_types: Iterable[int] = (1, 2, 4),
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = _normalize_title(keyword)
    if not query:
        return []
    allowed = {int(value) for value in subject_types}
    _, rows = _load_index()
    matched: list[tuple[int, int, dict[str, Any]]] = []
    for record in rows:
        if int(record.get("type") or 0) not in allowed:
            continue
        names = [_normalize_title(record.get("name_cn")), _normalize_title(record.get("name"))]
        # Archive's compact ``infobox`` field retains Japanese/Chinese/English
        # aliases that are not always present in ``name`` or ``name_cn``.
        # Searching those aliases is especially important for adult games whose
        # public API record may be hidden (for example, an English series title).
        infobox = str(record.get("infobox") or "")
        names.extend(
            _normalize_title(alias)
            for alias in re.findall(r"[\[【（(]\s*([^\]】）)]+?)\s*[\]】）)]", infobox)
        )
        if not any(query in name or name in query for name in names if name):
            continue
        exact = 1 if any(query == name for name in names if name) else 0
        rank = int(record.get("rank") or 1_000_000_000)
        matched.append((exact, -rank, record))
    matched.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [dict(item[2]) for item in matched[: max(1, int(limit))]]


def index_status() -> dict[str, Any]:
    metadata = _read_metadata()
    metadata["available"] = INDEX_PATH.exists()
    return metadata
