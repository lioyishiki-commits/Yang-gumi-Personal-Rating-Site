import unittest
from unittest.mock import patch

import restart_yanggumi as restart


class RestartYangGumiTest(unittest.TestCase):
    def test_owned_streamlit_requires_current_root_and_app(self):
        command = f'python -m streamlit run "{restart.ROOT / "app.py"}" --server.port 8501'
        self.assertTrue(restart._is_owned_streamlit(command))
        self.assertFalse(restart._is_owned_streamlit('python -m streamlit run C:\\other\\app.py'))
        self.assertFalse(restart._is_owned_streamlit(f'python "{restart.ROOT / "app.py"}"'))

    def test_unrelated_listener_is_never_terminated(self):
        with (
            patch.object(restart, "_process_command_line", return_value='python -m http.server 8501'),
            patch.object(restart.subprocess, "run") as run,
        ):
            with self.assertRaises(restart.RestartError):
                restart._stop_owned_site(123)
        run.assert_not_called()

    def test_restart_waits_for_owned_site_and_launches_hidden(self):
        with (
            patch.object(restart, "_healthy", side_effect=[True, False, False, True]),
            patch.object(restart, "_listener_pid", return_value=123),
            patch.object(restart, "stop_owned_site", return_value=123) as stop,
            patch.object(restart, "_launch_hidden", return_value=456) as launch,
        ):
            result = restart.restart_running_site(timeout=2)
        self.assertEqual(result, {"restarted": True, "old_pid": 123, "launcher_pid": 456})
        stop.assert_called_once_with(restart.PORT, timeout=2)
        launch.assert_called_once_with()

    def test_closed_site_is_not_started(self):
        with (
            patch.object(restart, "_healthy", return_value=False),
            patch.object(restart, "_launch_hidden") as launch,
        ):
            self.assertEqual(restart.restart_running_site(), {"restarted": False, "reason": "not_running"})
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
