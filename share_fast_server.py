# Yang-gumi release: 1.3.0
from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import share_assets
import share_static_export


ROOT = Path(__file__).resolve().parent
SITE_ROOT = ROOT / "data" / "remote_share_site"
STATIC_ROOT = ROOT / "static"
BUILD_LOCK = threading.Lock()
STOP_EVENT = threading.Event()
CURRENT_REVISION: tuple[int, int, int, int] | None = None
ACCESS_COOKIE_NAME = "yanggumi_share_session"


def rebuild_if_needed(force: bool = False) -> tuple[int, int, int, int]:
    global CURRENT_REVISION
    revision = share_assets.source_revision()
    if not force and revision == CURRENT_REVISION and (SITE_ROOT / "index.html").is_file():
        return revision
    with BUILD_LOCK:
        revision = share_assets.source_revision()
        if force or revision != CURRENT_REVISION or not (SITE_ROOT / "index.html").is_file():
            share_static_export.build_public_site(SITE_ROOT)
            CURRENT_REVISION = revision
    return revision


def _safe_file(root: Path, relative: str) -> Path | None:
    try:
        resolved = (root / unquote(relative)).resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


class ShareHandler(BaseHTTPRequestHandler):
    server_version = "YangGumiShare/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_bytes(
        self,
        payload: bytes,
        content_type: str,
        *,
        cache_control: str,
        status: HTTPStatus = HTTPStatus.OK,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        if "gzip" in self.headers.get("Accept-Encoding", "") and content_type.startswith(("text/", "application/json")):
            payload = gzip.compress(payload, compresslevel=6)
            encoding = "gzip"
        else:
            encoding = ""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _serve_file(self, path: Path, cache_control: str) -> None:
        try:
            payload = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send_bytes(payload, content_type, cache_control=cache_control)

    def _query_authorized(self, query: dict[str, list[str]]) -> bool:
        supplied = str((query.get("access") or [""])[0])
        expected = str(getattr(self.server, "share_token", ""))
        return bool(expected) and secrets.compare_digest(supplied, expected)

    def _cookie_authorized(self) -> bool:
        expected = str(getattr(self.server, "access_cookie", ""))
        if not expected:
            return False
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        supplied = cookie.get(ACCESS_COOKIE_NAME)
        return bool(supplied) and secrets.compare_digest(supplied.value, expected)

    def _authorized(self, query: dict[str, list[str]]) -> bool:
        return self._query_authorized(query) or self._cookie_authorized()

    def _session_cookie_header(self) -> str:
        value = str(getattr(self.server, "access_cookie", ""))
        cookie = f"{ACCESS_COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Strict"
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
        host = self.headers.get("Host", "").split(":", 1)[0].strip().lower()
        if forwarded_proto == "https" or host not in {"127.0.0.1", "localhost", "[::1]"}:
            cookie += "; Secure"
        return cookie

    def _forbidden(self, *, html: bool = False) -> None:
        if html:
            payload = "<!doctype html><meta charset=utf-8><title>链接无效</title><h1>这个只读分享链接无效或已经更换。</h1>".encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            payload = b'{"error":"forbidden"}'
            content_type = "application/json; charset=utf-8"
        self._send_bytes(
            payload,
            content_type,
            cache_control="no-store",
            status=HTTPStatus.FORBIDDEN,
        )

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path == "/_stcore/health":
            self._send_bytes(b"ok", "text/plain; charset=utf-8", cache_control="no-store")
            return
        if path == "/revision.json":
            if not self._authorized(query):
                self._forbidden()
                return
            revision = rebuild_if_needed()
            payload = json.dumps({"revision": revision}, separators=(",", ":")).encode("utf-8")
            self._send_bytes(payload, "application/json; charset=utf-8", cache_control="no-store")
            return
        if path == "/snapshot.json":
            if not self._authorized(query):
                self._forbidden()
                return
            rebuild_if_needed()
            self._serve_file(SITE_ROOT / "snapshot.json", "no-store")
            return
        if path.startswith("/app/static/"):
            asset = _safe_file(STATIC_ROOT, path.removeprefix("/app/static/"))
            if asset is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            relative = asset.relative_to(STATIC_ROOT).as_posix()
            cache = "public, max-age=31536000, immutable" if relative.startswith(("share_assets/", "daily_art/")) else "public, max-age=86400"
            self._serve_file(asset, cache)
            return
        if path in {"/", "/index.html"}:
            if not self._authorized(query):
                self._forbidden(html=True)
                return
            rebuild_if_needed()
            try:
                payload = (SITE_ROOT / "index.html").read_bytes()
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            headers: tuple[tuple[str, str], ...] = ()
            if self._query_authorized(query):
                headers = (("Set-Cookie", self._session_cookie_header()),)
            self._send_bytes(
                payload,
                "text/html; charset=utf-8",
                cache_control="no-store",
                extra_headers=headers,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def revision_watcher() -> None:
    while not STOP_EVENT.wait(5):
        try:
            rebuild_if_needed()
        except Exception:
            time.sleep(1)


def run(port: int, token: str) -> None:
    rebuild_if_needed(force=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), ShareHandler)
    server.daemon_threads = True
    server.share_token = token  # type: ignore[attr-defined]
    server.access_cookie = secrets.token_urlsafe(32)  # type: ignore[attr-defined]
    watcher = threading.Thread(target=revision_watcher, name="yanggumi-static-revision", daemon=True)
    watcher.start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        STOP_EVENT.set()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    token = os.getenv("YANGGUMI_SHARE_TOKEN", "")
    if not token:
        raise RuntimeError("YANGGUMI_SHARE_TOKEN is required")
    run(args.port, token)


if __name__ == "__main__":
    main()
