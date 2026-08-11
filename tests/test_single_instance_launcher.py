import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import restart_yanggumi
import start_yanggumi


class SingleInstanceLauncherTest(unittest.TestCase):
    def test_process_command_line_round_trips_chinese_install_path(self):
        command = f'python -m streamlit run "{restart_yanggumi.ROOT / "app.py"}"'
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        completed = mock.Mock(returncode=0, stdout=encoded)
        with mock.patch.object(restart_yanggumi.subprocess, "run", return_value=completed):
            self.assertEqual(restart_yanggumi._process_command_line(123), command)
            self.assertTrue(restart_yanggumi._is_owned_streamlit(command))

    def test_active_share_is_stopped_before_owned_site_and_resumed_later(self):
        with (
            mock.patch.object(start_yanggumi, "_share_should_resume", return_value=True),
            mock.patch.object(start_yanggumi.share_control, "stop_remote_share") as stop_share,
            mock.patch.object(start_yanggumi.restart_yanggumi, "_listener_pid", return_value=123),
            mock.patch.object(start_yanggumi.restart_yanggumi, "replace_port_listener", return_value=123) as stop_site,
            mock.patch.object(start_yanggumi, "port_is_open", return_value=False),
        ):
            self.assertTrue(start_yanggumi.prepare_single_instance())
        stop_share.assert_called_once_with(timeout=5)
        stop_site.assert_called_once_with(start_yanggumi.PORT, timeout=15)

    def test_official_chrome_is_used_for_local_site(self):
        with (
            mock.patch.object(start_yanggumi.Path, "is_file", return_value=True),
            mock.patch.object(start_yanggumi.subprocess, "Popen") as popen,
        ):
            self.assertTrue(start_yanggumi.open_site_in_browser())
        popen.assert_called_once_with(
            [str(start_yanggumi.OFFICIAL_CHROME_PATH), "--new-tab", start_yanggumi.URL],
            cwd=start_yanggumi.ROOT,
        )

    def test_windows_default_browser_is_used_when_official_chrome_is_absent(self):
        with (
            mock.patch.object(start_yanggumi.Path, "is_file", return_value=False),
            mock.patch.object(start_yanggumi.sys, "platform", "win32"),
            mock.patch.object(start_yanggumi.os, "startfile", create=True) as startfile,
        ):
            self.assertTrue(start_yanggumi.open_site_in_browser())
        startfile.assert_called_once_with(start_yanggumi.URL)

    def test_edge_is_used_when_windows_default_browser_handler_fails(self):
        chrome_path = mock.MagicMock()
        chrome_path.is_file.return_value = False
        edge_path = mock.MagicMock()
        edge_path.is_file.return_value = True
        edge_path.__str__.return_value = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

        with (
            mock.patch.object(start_yanggumi, "OFFICIAL_CHROME_PATH", chrome_path),
            mock.patch.object(start_yanggumi, "FALLBACK_BROWSER_PATHS", (("Microsoft Edge", edge_path),)),
            mock.patch.object(start_yanggumi.sys, "platform", "win32"),
            mock.patch.object(
                start_yanggumi.os,
                "startfile",
                side_effect=OSError("missing URL handler"),
                create=True,
            ),
            mock.patch.object(start_yanggumi.subprocess, "Popen") as popen,
        ):
            self.assertTrue(start_yanggumi.open_site_in_browser())
        popen.assert_called_once_with([str(edge_path), start_yanggumi.URL], cwd=start_yanggumi.ROOT)

    def test_browser_open_reports_failure_when_no_supported_handler_exists(self):
        with (
            mock.patch.object(start_yanggumi.Path, "is_file", return_value=False),
            mock.patch.object(start_yanggumi.sys, "platform", "win32"),
            mock.patch.object(
                start_yanggumi.os,
                "startfile",
                side_effect=OSError("missing URL handler"),
                create=True,
            ),
        ):
            self.assertFalse(start_yanggumi.open_site_in_browser())

    def test_transient_ghost_listener_can_release_without_killing_unknown_process(self):
        with (
            mock.patch.object(start_yanggumi, "_share_should_resume", return_value=False),
            mock.patch.object(start_yanggumi.restart_yanggumi, "_listener_pid", return_value=None),
            mock.patch.object(start_yanggumi, "port_is_open", side_effect=[True, False, False]),
            mock.patch.object(start_yanggumi.time, "sleep"),
        ):
            self.assertFalse(start_yanggumi.prepare_single_instance())

    def test_permanent_unidentifiable_listener_reports_failure(self):
        with (
            mock.patch.object(start_yanggumi, "_share_should_resume", return_value=False),
            mock.patch.object(start_yanggumi.restart_yanggumi, "_listener_pid", return_value=None),
            mock.patch.object(start_yanggumi.restart_yanggumi, "replace_port_listener") as stop_site,
            mock.patch.object(start_yanggumi, "port_is_open", return_value=True),
            mock.patch.object(start_yanggumi.time, "monotonic", side_effect=[0.0, 9.0]),
        ):
            with self.assertRaises(start_yanggumi.restart_yanggumi.RestartError):
                start_yanggumi.prepare_single_instance()
        stop_site.assert_not_called()

    def test_unowned_listener_is_force_terminated_for_desktop_launch(self):
        completed = mock.Mock(returncode=0)
        with (
            mock.patch.object(restart_yanggumi, "_listener_pid", side_effect=[321, None]),
            mock.patch.object(restart_yanggumi, "_process_command_line", return_value="python other_site.py"),
            mock.patch.object(restart_yanggumi.subprocess, "run", return_value=completed) as run,
            mock.patch.object(restart_yanggumi.time, "sleep"),
        ):
            self.assertEqual(restart_yanggumi.replace_port_listener(8501), 321)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "321", "/T", "/F"])

    def test_taskkill_nonzero_is_success_when_listener_already_disappeared(self):
        completed = mock.Mock(returncode=128)
        with (
            mock.patch.object(restart_yanggumi, "_listener_pid", side_effect=[321, None, None]),
            mock.patch.object(restart_yanggumi, "_process_command_line", return_value="python other_site.py"),
            mock.patch.object(restart_yanggumi.subprocess, "run", return_value=completed),
            mock.patch.object(restart_yanggumi.time, "sleep"),
        ):
            self.assertEqual(restart_yanggumi.replace_port_listener(8501), 321)

    def test_taskkill_nonzero_still_fails_when_same_listener_remains(self):
        completed = mock.Mock(returncode=5)
        with (
            mock.patch.object(restart_yanggumi, "_listener_pid", side_effect=[321, 321]),
            mock.patch.object(restart_yanggumi, "_process_command_line", return_value="python other_site.py"),
            mock.patch.object(restart_yanggumi.subprocess, "run", return_value=completed),
        ):
            with self.assertRaises(restart_yanggumi.RestartError):
                restart_yanggumi.replace_port_listener(8501)

    def test_replaced_launcher_consumes_only_its_own_signal(self):
        with tempfile.TemporaryDirectory() as temp:
            signal = Path(temp) / "replace.signal"
            with mock.patch.object(restart_yanggumi, "REPLACE_SIGNAL_PATH", signal):
                restart_yanggumi.mark_replacement(123)
                self.assertFalse(restart_yanggumi.consume_replacement(456))
                self.assertTrue(signal.exists())
                self.assertTrue(restart_yanggumi.consume_replacement(123))
                self.assertFalse(signal.exists())

    def test_stop_owned_site_waits_until_fixed_port_is_released(self):
        with (
            mock.patch.object(restart_yanggumi, "_listener_pid", side_effect=[123, None]),
            mock.patch.object(restart_yanggumi, "_stop_owned_site") as stop,
        ):
            self.assertEqual(restart_yanggumi.stop_owned_site(8501), 123)
        stop.assert_called_once_with(123)

    def test_launcher_batch_is_cmd_safe_utf8_bom_and_crlf(self):
        batch = Path(start_yanggumi.__file__).with_name("启动 Yang-gumi.bat").read_bytes()
        self.assertTrue(batch.startswith(b"\xef\xbb\xbf"))
        body = batch[3:]
        self.assertNotIn(b"\n", body.replace(b"\r\n", b""))


if __name__ == "__main__":
    unittest.main()
