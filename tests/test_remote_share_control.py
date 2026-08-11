# Yang-gumi release: 1.3.0
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import share_control
import share_public


class RemoteShareSecurityTests(unittest.TestCase):
    def test_keep_alive_opt_in_is_persistent_and_explicit_stop_clears_it(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "keep-alive.json"
            status = Path(temp) / "status.json"
            status.write_text(json.dumps({"state": "stopped", "pid": None}), encoding="utf-8")
            with mock.patch.object(share_control, "KEEP_ALIVE_PATH", marker), mock.patch.object(
                share_control, "STATUS_PATH", status
            ), mock.patch.object(share_control, "STOP_PATH", Path(temp) / "stop"), mock.patch.object(
                share_control, "PID_PATH", Path(temp) / "pid"
            ), mock.patch.object(share_control, "process_is_running", return_value=False):
                share_control._set_keep_alive(True)
                self.assertTrue(share_control.keep_alive_enabled())
                share_control.stop_remote_share(timeout=0)
                self.assertFalse(share_control.keep_alive_enabled())

    def test_supervisor_restarts_only_an_opted_in_dead_share(self):
        stopped = {"state": "error", "pid": None, "public_url": ""}
        restarted = {"state": "starting", "pid": 1234}
        share_control._last_supervisor_start = 0.0
        with mock.patch.object(share_control, "keep_alive_enabled", return_value=True), mock.patch.object(
            share_control, "read_status", return_value=stopped
        ), mock.patch.object(
            share_control, "process_is_running", return_value=False
        ), mock.patch.object(
            share_control, "start_remote_share", return_value=restarted
        ) as start:
            result = share_control.ensure_remote_share_running(retry_interval=1)
        self.assertEqual(result, restarted)
        start.assert_called_once_with(keep_alive=False)

        with mock.patch.object(share_control, "keep_alive_enabled", return_value=False), mock.patch.object(
            share_control, "read_status", return_value=stopped
        ), mock.patch.object(share_control, "start_remote_share") as start:
            self.assertEqual(share_control.ensure_remote_share_running(), stopped)
        start.assert_not_called()

    def test_each_share_token_is_fresh_and_has_at_least_256_bits(self):
        with tempfile.TemporaryDirectory() as temp:
            token_path = Path(temp) / "token.txt"
            with mock.patch.object(share_public, "TOKEN_PATH", token_path), mock.patch.object(
                share_public, "DATA_DIR", Path(temp)
            ):
                first = share_public.share_token()
                second = share_public.share_token()
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 43)
        self.assertGreaterEqual(len(second), 43)

    def test_streamlit_reuse_keeps_the_owner_process_writable(self):
        env = share_public.streamlit_environment("masked-test-token")
        self.assertNotIn("YANGGUMI_READ_ONLY", env)
        self.assertNotIn("YANGGUMI_SHARE_TOKEN", env)

    def test_status_reader_does_not_report_dead_process_as_running(self):
        with tempfile.TemporaryDirectory() as temp:
            status_path = Path(temp) / "status.json"
            status_path.write_text(
                json.dumps({"state": "running", "pid": 99999999, "public_url": "https://example.invalid/?access=secret"}),
                encoding="utf-8",
            )
            with mock.patch.object(share_control, "STATUS_PATH", status_path), mock.patch.object(
                share_control, "process_is_running", return_value=False
            ):
                status = share_control.read_status()
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["public_url"], "")

    def test_cloudflare_command_uses_loopback_origin(self):
        source = Path(share_public.__file__).read_text(encoding="utf-8")
        self.assertIn('"--url", f"http://127.0.0.1:{port}"', source)
        self.assertNotIn('"--server.address", "0.0.0.0"', source)

    def test_public_health_requires_the_streamlit_ok_body(self):
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b"No matching tunnel domain"
        response.__enter__.return_value = response
        with mock.patch("share_public.urllib.request.urlopen", return_value=response):
            self.assertFalse(share_public.public_streamlit_server_ready("https://example.invalid"))
        response.read.return_value = b"ok"
        with mock.patch("share_public.urllib.request.urlopen", return_value=response):
            self.assertTrue(share_public.public_streamlit_server_ready("https://example.invalid"))

    def test_public_health_drops_access_query_before_appending_health_path(self):
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b"ok"
        response.__enter__.return_value = response
        public_url = "https://example.invalid/share/?access=masked&_xpos_continue=1"

        with mock.patch("share_public.urllib.request.urlopen", return_value=response) as urlopen:
            self.assertTrue(share_public.public_streamlit_server_ready(public_url))

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/share/__yanggumi_share_health")

    def test_public_auth_health_requires_cookie_and_streamlit_html(self):
        response = mock.MagicMock()
        response.status = 200
        response.headers = {"Content-Type": "text/html; charset=utf-8"}
        response.read.return_value = b"<html><title>Streamlit</title></html>"
        response.__enter__.return_value = response
        jar = mock.MagicMock()
        cookie = mock.MagicMock()
        cookie.name = share_public.SHARE_COOKIE_NAME
        jar.__iter__.return_value = [cookie]
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("share_public.http.cookiejar.CookieJar", return_value=jar), mock.patch(
            "share_public.urllib.request.build_opener", return_value=opener
        ):
            self.assertTrue(
                share_public.public_authorized_streamlit_ready(
                    "https://example.invalid", "masked-token"
                )
            )

        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/?access=masked-token")

    def test_unstable_or_browser_blocked_providers_are_disabled_for_new_visitor_links(self):
        self.assertEqual(
            {"Expose", "Hostc", "Wormhole", "XPOS"},
            share_public.DISABLED_TUNNEL_PROVIDERS,
        )

    def test_websocket_health_rejects_non_http_urls(self):
        self.assertFalse(share_public.public_streamlit_websocket_ready("file:///tmp/not-a-tunnel"))
        source = Path(share_public.__file__).read_text(encoding="utf-8")
        self.assertIn("proxy_aware_websocket_connect", source)
        self.assertIn("GET /_stcore/stream HTTP/1.1", source)
        self.assertIn("sec-websocket-accept", source)
        self.assertIn("Sec-WebSocket-Protocol: streamlit", source)
        self.assertIn("sec-websocket-protocol: streamlit", source)

    def test_websocket_health_closes_with_a_masked_normal_close_frame(self):
        connection = mock.MagicMock()
        connection.recv.return_value = b"\x88\x02\x03\xe8"

        share_public._close_websocket_cleanly(connection)

        frame = connection.sendall.call_args.args[0]
        self.assertEqual(frame[0], 0x88)
        self.assertEqual(frame[1] & 0x80, 0x80)
        self.assertEqual(frame[1] & 0x7F, 2)
        mask = frame[2:6]
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(frame[6:]))
        self.assertEqual(payload, b"\x03\xe8")
        connection.settimeout.assert_called_once_with(0.5)

    def test_portable_plain_tunnel_is_attempted_before_serveo_and_legacy_fallbacks(self):
        source = Path(share_public.__file__).read_text(encoding="utf-8")
        function_source = source[source.index("def start_tunnel("):source.index("def _stop_requested()")]
        self.assertLess(function_source.index("PLAIN_TUNNEL_CLIENT_PATH"), function_source.index('"Serveo" not in excluded'))
        self.assertLess(function_source.index('"Serveo" not in excluded'), function_source.index("RUNLOCAL_CLIENT_PATH"))
        self.assertLess(function_source.index("RUNLOCAL_CLIENT_PATH"), function_source.index("HOSTC_RUNTIME_PATH"))
        self.assertLess(function_source.index("HOSTC_RUNTIME_PATH"), function_source.index("XPOS_IDENTITY_PATH"))
        self.assertLess(function_source.index("XPOS_IDENTITY_PATH"), function_source.index("WORMHOLE_PATH"))
        self.assertLess(function_source.index("WORMHOLE_PATH"), function_source.index("EXPOSE_PATH"))
        self.assertLess(function_source.index("EXPOSE_PATH"), function_source.index("CLOUDFLARED_PATH"))
        self.assertLess(function_source.index("CLOUDFLARED_PATH"), function_source.index('"free.pinggy.io"'))
        self.assertLess(function_source.index('"free.pinggy.io"'), function_source.index('"nokey@localhost.run"'))
        self.assertIn("PINGGY_ASKPASS_PATH", function_source)
        self.assertIn('SSH_ASKPASS_REQUIRE="force"', function_source)
        self.assertIn("nokey@localhost.run", function_source)
        self.assertNotIn("tunnelmole", function_source.lower())
        self.assertIn("wait_for_public_streamlit", function_source)
        self.assertIn('"XPOS" not in excluded', function_source)
        self.assertIn('"Wormhole" not in excluded', function_source)
        self.assertIn("DISABLED_TUNNEL_PROVIDERS", function_source)
        self.assertIn("excluded_providers=excluded_providers", source)

    def test_plain_tunnel_public_urls_are_recognized(self):
        self.assertIsNotNone(
            share_public.TUNNEL_URL_RE.search("https://yanggumi-0123456789abcdef.plaintunnel.com")
        )

    def test_plain_tunnel_subdomain_is_stable_and_machine_local(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plain_tunnel_subdomain.txt"
            first = share_public.plain_tunnel_subdomain(path)
            second = share_public.plain_tunnel_subdomain(path)
            self.assertRegex(first, r"^yanggumi-[a-f0-9]{16}$")
            self.assertEqual(first, second)
            self.assertEqual(path.read_text(encoding="ascii").strip(), first)

    def test_serveo_public_urls_and_warning_safe_path_are_recognized(self):
        base = "https://sample.serveousercontent.com"
        self.assertIsNotNone(share_public.TUNNEL_URL_RE.search(base))
        self.assertEqual(
            share_public.public_access_url(base, "masked-token"),
            "https://sample.serveousercontent.com/_yanggumi_share/masked-token"
            "?serveo-skip-browser-warning=true",
        )

    def test_owner_launcher_refreshes_the_standalone_visitor_command(self):
        source = (
            "@echo off\r\n"
            ":visitor\r\n"
            "powershell.exe -Command \"old\"\r\n"
            "exit /b 0\r\n"
            "\r\n"
            ":owner\r\n"
            "echo owner must remain\r\n"
        )
        updated = share_public._refresh_owner_visitor_block(source)
        self.assertIn("serveousercontent\\.com", updated)
        self.assertIn("_yanggumi_share/", updated)
        self.assertIn(":owner\r\necho owner must remain", updated)
        self.assertNotIn('-Command "old"', updated)

    def test_runlocal_public_urls_are_recognized(self):
        self.assertIsNotNone(share_public.TUNNEL_URL_RE.search("https://plain-otter.runlocal.eu"))

    def test_hostc_public_urls_are_recognized(self):
        self.assertIsNotNone(share_public.TUNNEL_URL_RE.search("https://t-example.hostc.dev"))

    def test_expose_public_urls_are_recognized(self):
        self.assertIsNotNone(share_public.TUNNEL_URL_RE.search("https://abc123.expose.host"))

    def test_wormhole_public_urls_are_recognized(self):
        self.assertIsNotNone(share_public.TUNNEL_URL_RE.search("https://abc123.wormhole.bar"))

    def test_xpos_public_urls_are_recognized_and_warning_is_bypassed(self):
        base = "https://abc123.xpos.to"
        self.assertIsNotNone(share_public.TUNNEL_URL_RE.search(base))
        self.assertEqual(
            share_public.public_access_url(base, "token"),
            "https://abc123.xpos.to/?_xpos_continue=1&access=token",
        )

    def test_pinggy_public_urls_are_recognized(self):
        self.assertIsNotNone(share_public.TUNNEL_URL_RE.search("https://sample.free.pinggy.net"))
        self.assertIsNotNone(share_public.TUNNEL_URL_RE.search("https://sample.run.pinggy-free.link"))

    def test_share_instance_lock_rejects_a_second_process(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            share_public, "INSTANCE_LOCK_PATH", Path(temp) / "share.lock"
        ), mock.patch.object(share_public, "DATA_DIR", Path(temp)):
            first = share_public._acquire_instance_lock()
            try:
                self.assertIsNotNone(first)
                second = share_public._acquire_instance_lock()
                self.assertIsNone(second)
            finally:
                share_public._release_instance_lock(first)
            third = share_public._acquire_instance_lock()
            try:
                self.assertIsNotNone(third)
            finally:
                share_public._release_instance_lock(third)

    def test_transient_health_failures_keep_the_same_public_link(self):
        public_url = "https://stable-for-this-session.lhr.life/?access=masked"
        failures = 0
        with mock.patch("share_public._write_status") as write_status, mock.patch.object(
            share_public, "LOGGER"
        ):
            for _ in range(share_public.PUBLIC_HEALTH_DEGRADED_AFTER):
                failures = share_public._update_public_health_status(False, failures, public_url)
        self.assertEqual(failures, share_public.PUBLIC_HEALTH_DEGRADED_AFTER)
        write_status.assert_called_once()
        changes = write_status.call_args.kwargs
        self.assertEqual(changes["state"], "degraded")
        self.assertEqual(changes["public_url"], public_url)
        self.assertEqual(changes["tunnel_state"], "connected")
        self.assertIn("未更换链接", changes["last_error"])

    def test_recovered_health_restores_running_state_on_the_same_link(self):
        public_url = "https://stable-for-this-session.lhr.life/?access=masked"
        with mock.patch("share_public._write_status") as write_status, mock.patch.object(
            share_public, "LOGGER"
        ):
            failures = share_public._update_public_health_status(
                True, share_public.PUBLIC_HEALTH_DEGRADED_AFTER, public_url
            )
        self.assertEqual(failures, 0)
        changes = write_status.call_args.kwargs
        self.assertEqual(changes["state"], "running")
        self.assertEqual(changes["public_url"], public_url)
        self.assertEqual(changes["last_error"], "")

    def test_stale_recyclable_tunnel_is_killed_after_repeated_health_failures(self):
        source = Path(share_public.__file__).read_text(encoding="utf-8")
        monitor_start = source.index("while not _stop_requested():")
        monitor = source[monitor_start:source.index("tunnel = None", monitor_start)]
        self.assertIn("_update_public_health_status", monitor)
        self.assertIn("_should_recycle_stale_tunnel", monitor)
        self.assertIn("_terminate(tunnel)", monitor)
        self.assertIn('"TCPKeepAlive=yes"', source)
        self.assertIn('"ServerAliveCountMax=12"', source)

    def test_plain_tunnel_disconnect_is_delegated_to_the_share_supervisor(self):
        share_source = Path(share_public.__file__).read_text(encoding="utf-8")
        client_source = Path(share_public.PLAIN_TUNNEL_CLIENT_PATH).read_text(encoding="utf-8")

        self.assertIn('"--exit-on-disconnect"', share_source)
        self.assertIn('parser.add_argument(\n        "--exit-on-disconnect"', client_source)
        self.assertIn("if self.exit_on_disconnect:", client_source)
        self.assertEqual(share_public.PUBLIC_HEALTH_DEGRADED_AFTER, 1)

    def test_optional_locator_cannot_delay_tunnel_exit_detection_for_tens_of_seconds(self):
        source = Path(share_public.__file__).read_text(encoding="utf-8")
        monitor_start = source.index("while not _stop_requested():")
        monitor_end = source.index("tunnel = None", monitor_start)
        monitor = source[monitor_start:monitor_end]

        self.assertEqual(share_public.LOCATOR_ATTEMPTS, 1)
        self.assertLessEqual(share_public.LOCATOR_REQUEST_TIMEOUT_SECONDS, 2.5)
        self.assertLess(monitor.index("tunnel.poll()"), monitor.index("sync_public_locator(final_url)"))
        self.assertLess(monitor.index("sync_public_locator(final_url)"), monitor.index("continue"))

    def test_stale_localhost_run_is_recycled_through_the_stable_locator(self):
        threshold = share_public.PUBLIC_HEALTH_RECYCLE_AFTER
        self.assertEqual(threshold, 3)
        self.assertTrue(share_public._should_recycle_stale_tunnel("Wormhole", threshold))
        self.assertTrue(share_public._should_recycle_stale_tunnel("PlainTunnel", threshold))
        self.assertTrue(share_public._should_recycle_stale_tunnel("localhost.run", threshold))
        self.assertFalse(share_public._should_recycle_stale_tunnel("XPOS", threshold))
        self.assertFalse(share_public._should_recycle_stale_tunnel("Wormhole", threshold - 1))
        self.assertFalse(share_public._should_recycle_stale_tunnel("Expose", threshold))
        self.assertFalse(share_public._should_recycle_stale_tunnel("Cloudflare", threshold))
        self.assertFalse(share_public._should_recycle_stale_tunnel("Pinggy", threshold))
        self.assertFalse(share_public._should_recycle_stale_tunnel("Runlocal", threshold))

    def test_unhealthy_local_upstream_moves_proxy_to_a_fresh_streamlit_port(self):
        old_proxy = mock.MagicMock()
        replacement_streamlit = mock.MagicMock()
        replacement_proxy = mock.MagicMock()
        job = mock.MagicMock()
        with mock.patch.object(
            share_public, "find_available_streamlit_port", return_value=8505
        ), mock.patch.object(
            share_public, "_start_owned_streamlit", return_value=replacement_streamlit
        ) as start_streamlit, mock.patch.object(
            share_public, "_start_proxy_process", return_value=replacement_proxy
        ) as start_proxy, mock.patch.object(share_public, "_terminate") as terminate:
            proxy, streamlit, port = share_public._recover_local_upstream(
                old_proxy,
                None,
                job,
            )
        self.assertIs(proxy, replacement_proxy)
        self.assertIs(streamlit, replacement_streamlit)
        self.assertEqual(port, 8505)
        start_streamlit.assert_called_once_with(8505, job)
        terminate.assert_called_once_with(old_proxy)
        start_proxy.assert_called_once_with(share_public.PORT, 8505, job)

    def test_share_monitor_recovers_local_upstream_before_public_probes(self):
        source = Path(share_public.__file__).read_text(encoding="utf-8")
        monitor_start = source.index("while not _stop_requested():")
        monitor = source[monitor_start:source.index("tunnel = None", monitor_start)]
        self.assertIn("_recover_local_upstream", monitor)
        self.assertIn("streamlit_server_ready(port=upstream_port)", monitor)
        self.assertNotIn("streamlit_server_ready(port=MAIN_APP_PORT)", monitor)

    def test_public_locator_is_persistent_and_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "locator.json"
            locator = "https://jsonblob.com/api/jsonBlob/019f9ec2-2425-7b31-b346-df11f1e56c95"
            calls: list[tuple[str, str, dict | None]] = []
            stored: dict = {}

            def request(url, *, method, payload=None, timeout=10.0):
                calls.append((url, method, payload))
                if method == "POST":
                    stored.update(payload or {})
                    return 201, {"location": locator}, dict(stored)
                if method == "PUT":
                    stored.clear()
                    stored.update(payload or {})
                    return 200, {}, dict(stored)
                return 200, {}, dict(stored)

            with mock.patch.object(share_public, "LOCATOR_CONFIG_PATH", config_path), mock.patch.object(
                share_public, "_locator_request", side_effect=request
            ):
                first_url, first_synced = share_public.sync_public_locator("https://one.xpos.to/?access=token")
                second_url, second_synced = share_public.sync_public_locator("https://two.hostc.dev/?access=token")
        self.assertTrue(first_synced)
        self.assertTrue(second_synced)
        self.assertEqual(first_url, locator)
        self.assertEqual(second_url, locator)
        self.assertEqual([method for _url, method, _payload in calls], ["POST", "GET", "PUT", "GET"])
        self.assertEqual(stored["url"], "https://two.hostc.dev/?access=token")

    def test_missing_stored_locator_is_recreated_immediately(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "locator.json"
            old_locator = "https://jsonblob.com/api/jsonBlob/019f9ec2-2425-7b31-b346-df11f1e56c95"
            new_locator = "https://jsonblob.com/api/jsonBlob/019f9ec2-2425-7b31-b346-df11f1e56c96"
            config_path.write_text(json.dumps({"url": old_locator}), encoding="utf-8")
            calls: list[tuple[str, str]] = []
            stored: dict = {}

            def request(url, *, method, payload=None, timeout=10.0):
                calls.append((url, method))
                if url == old_locator:
                    raise urllib.error.HTTPError(url, 404, "gone", {}, None)
                if method == "POST":
                    stored.update(payload or {})
                    return 201, {"location": new_locator}, dict(stored)
                return 200, {}, dict(stored)

            with mock.patch.object(share_public, "LOCATOR_CONFIG_PATH", config_path), mock.patch.object(
                share_public, "_locator_request", side_effect=request
            ), mock.patch.object(share_public.time, "sleep"):
                locator_url, synced = share_public.sync_public_locator(
                    "https://current.xpos.to/?access=token"
                )

            self.assertTrue(synced)
            self.assertEqual(locator_url, new_locator)
            self.assertEqual(
                calls,
                [
                    (old_locator, "PUT"),
                    (share_public.LOCATOR_API_URL, "POST"),
                    (new_locator, "GET"),
                ],
            )
            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8"))["url"],
                new_locator,
            )

    def test_invalid_locator_is_never_embedded(self):
        self.assertTrue(
            share_public._valid_locator_url(
                "https://jsonblob.com/api/jsonBlob/019f9ec2-2425-7b31-b346-df11f1e56c95"
            )
        )
        self.assertFalse(share_public._valid_locator_url("https://example.invalid/redirect"))
        self.assertFalse(share_public._valid_locator_url("http://jsonblob.com/api/jsonBlob/not-safe"))

    def test_share_transport_uses_the_existing_streamlit_readonly_ui(self):
        source = Path(share_public.__file__).read_text(encoding="utf-8")
        command = share_public.streamlit_command(18648)
        self.assertIn(str(Path(share_public.__file__).parent / "app.py"), command)
        self.assertNotIn("app_public.py", " ".join(command))
        self.assertIn("--server.enableWebsocketCompression", command)
        self.assertIn("--server.fileWatcherType", command)
        self.assertNotIn("share_fast_server.py", " ".join(command))
        proxy = share_public.proxy_command(18649, 18648)
        self.assertIn("share_proxy_server.py", " ".join(proxy))
        self.assertIn("streamlit_server_ready(port=MAIN_APP_PORT)", source)
        monitor_start = source.index("while not _stop_requested():")
        monitor = source[monitor_start:source.index("tunnel = None", monitor_start)]
        self.assertIn("public_streamlit_server_ready(base_url)", monitor)
        self.assertIn("public_authorized_streamlit_ready(base_url, token)", monitor)
        self.assertIn("public_streamlit_websocket_ready(base_url, token=token)", monitor)

    def test_dual_mode_launcher_embeds_current_public_url(self):
        root = Path(share_public.__file__).parent
        launcher = root / "启动只读分享.bat"
        source = launcher.read_text(encoding="ascii")
        self.assertIn('if exist "%~dp0share_public.py" goto owner', source)
        self.assertIn("goto visitor", source)
        self.assertNotIn('start "" "%YANGGUMI_PUBLIC_URL%"', source)
        self.assertIn("$p.UseShellExecute=$true", source)
        self.assertIn("SHIKISHARE_VISITOR_DRY_RUN", source)
        self.assertIn('set "YANGGUMI_SHARE_LOCATOR=', source)
        self.assertIn("Invoke-RestMethod", source)
        updated = share_public.replace_public_url(source, "https://unit-test.example/?access=token")
        self.assertIn(
            'set "YANGGUMI_PUBLIC_URL=https://unit-test.example/?access=token"',
            updated,
        )
        updated = share_public.replace_public_locator(
            updated,
            "https://jsonblob.com/api/jsonBlob/019f9ec2-2425-7b31-b346-df11f1e56c95",
        )
        self.assertIn(
            'set "YANGGUMI_SHARE_LOCATOR=https://jsonblob.com/api/jsonBlob/019f9ec2-2425-7b31-b346-df11f1e56c95"',
            updated,
        )

    def test_main_panel_keeps_showing_the_link_while_degraded(self):
        app_source = (Path(share_public.__file__).parent / "app.py").read_text(encoding="utf-8")
        self.assertIn('running = state in {"running", "degraded"}', app_source)
        self.assertIn("保留下方原链接等待恢复", app_source)


if __name__ == "__main__":
    unittest.main()
