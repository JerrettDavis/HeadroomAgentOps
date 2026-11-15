import unittest
from unittest.mock import patch

from agentops.tray import start_tray


class TrayTests(unittest.TestCase):
    def test_non_windows_does_not_launch(self):
        with patch('agentops.tray.sys.platform', 'linux'), patch('agentops.tray.subprocess.Popen') as spawn:
            self.assertIsNone(start_tray('127.0.0.1', 8765))
            spawn.assert_not_called()

    def test_windows_starts_hidden_companion_bound_to_panel(self):
        with patch('agentops.tray.sys.platform', 'win32'), patch('agentops.tray.os.getpid', return_value=123), patch('agentops.tray.subprocess.Popen') as spawn:
            start_tray('::1', 8765)
            args = spawn.call_args.args[0]
            self.assertIn('http://[::1]:8765', args)
            self.assertIn('123', args)
            self.assertIn('Hidden', args)
            self.assertTrue(spawn.call_args.kwargs['creationflags'])

    def test_failure_does_not_stop_dashboard(self):
        with self.assertLogs('agentops.tray', level='WARNING'), patch('agentops.tray.sys.platform', 'win32'), patch('agentops.tray.subprocess.Popen', side_effect=OSError('unavailable')):
            self.assertIsNone(start_tray('127.0.0.1', 8765))
