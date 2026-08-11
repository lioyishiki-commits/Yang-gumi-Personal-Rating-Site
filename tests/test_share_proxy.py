from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

import database as db
from share_auth import SHARE_COOKIE_NAME, session_cookie_value
from share_proxy_server import (
    READ_ONLY_HEADER,
    STREAMLIT_WEBSOCKET_CONSTRUCTORS,
    create_app,
)


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen_readonly_header = ""
    seen_accept_encoding = ""

    def do_GET(self) -> None:
        type(self).seen_readonly_header = self.headers.get(READ_ONLY_HEADER, "")
        type(self).seen_accept_encoding = self.headers.get("Accept-Encoding", "")
        if self.path.startswith("/static/"):
            payload = ("const yanggumi = 'share proxy';\n" * 200).encode("utf-8")
            content_type = "application/javascript"
        elif self.path.startswith("/image"):
            payload = b"WEBP" * 600
            content_type = "image/webp"
        else:
            payload = ("<!doctype html><title>Yang-gumi</title>" + "x" * 1400).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ShareProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.thread.join(timeout=3)

    def setUp(self) -> None:
        self.token = "unit-test-token"
        self.client = TestClient(create_app(self.token, self.upstream.server_port))
        self.cookie = f"{SHARE_COOKIE_NAME}={session_cookie_value(self.token)}"

    def test_token_is_exchanged_for_a_secure_http_only_cookie(self):
        response = self.client.get(
            "/?access=unit-test-token",
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("access=", response.headers["location"])
        cookie = response.headers["set-cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertNotIn(self.token, cookie)

    def test_path_token_supports_tunnel_warning_redirects(self):
        response = self.client.get(
            "/_yanggumi_share/unit-test-token",
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        self.assertIn(SHARE_COOKIE_NAME, response.headers["set-cookie"])
        self.assertNotIn(self.token, response.headers["set-cookie"])

    def test_unauthorized_data_paths_are_forbidden(self):
        for path in ("/", "/snapshot.json", "/revision.json"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_authorized_requests_are_readonly_cached_and_gzipped(self):
        root = self.client.get("/", headers={"Cookie": self.cookie})
        self.assertEqual(root.status_code, 200)
        self.assertEqual(root.headers["cache-control"], "no-store")
        self.assertEqual(_UpstreamHandler.seen_readonly_header, "1")
        self.assertEqual(_UpstreamHandler.seen_accept_encoding, "identity")

        static = self.client.get(
            "/static/index.js",
            headers={"Cookie": self.cookie, "Accept-Encoding": "gzip"},
        )
        self.assertEqual(static.status_code, 200)
        self.assertEqual(static.headers["content-encoding"], "gzip")
        self.assertEqual(static.headers["cache-control"], "public, max-age=31536000, immutable")

        image = self.client.get(
            "/image.webp",
            headers={"Cookie": self.cookie, "Accept-Encoding": "gzip"},
        )
        self.assertEqual(image.status_code, 200)
        self.assertNotIn("content-encoding", image.headers)
        self.assertEqual(image.headers["content-type"], "image/webp")
        self.assertEqual(self.client.post("/", headers={"Cookie": self.cookie}).status_code, 405)

    def test_entry_bundle_omits_streamlit_subprotocol_only_for_compatible_tunnels(self):
        source, replacement = STREAMLIT_WEBSOCKET_CONSTRUCTORS[0]
        with mock.patch.object(
            _UpstreamHandler,
            "do_GET",
            autospec=True,
        ) as upstream_get:
            def respond(handler: BaseHTTPRequestHandler) -> None:
                payload = b"const prefix=1;" + source + b";const suffix=2;"
                handler.send_response(200)
                handler.send_header("Content-Type", "application/javascript")
                handler.send_header("Content-Length", str(len(payload)))
                handler.end_headers()
                handler.wfile.write(payload)

            upstream_get.side_effect = respond
            response = self.client.get(
                "/static/js/index.unit.js",
                headers={"Cookie": self.cookie, "Accept-Encoding": "identity"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(replacement, response.content)
        self.assertNotIn(source, response.content)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_database_context_uses_sqlite_readonly_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE values_table(value TEXT)")
            connection.commit()
            connection.close()
            with mock.patch.object(db, "DB_PATH", path):
                db.set_read_only_mode(True)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        with db.connect() as readonly:
                            readonly.execute("INSERT INTO values_table(value) VALUES('blocked')")
                finally:
                    db.set_read_only_mode(False)


if __name__ == "__main__":
    unittest.main()
