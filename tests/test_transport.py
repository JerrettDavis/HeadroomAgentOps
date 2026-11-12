import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agentops.github import gh_transport


class TransportTests(unittest.TestCase):
    def test_read_only_tls_handshake_timeout_is_retried(self):
        responses = [SimpleNamespace(returncode=1, stderr='net/http: TLS handshake timeout', stdout=''),
                     SimpleNamespace(returncode=0, stderr='', stdout='{"ok":true}')]
        with patch('agentops.github.subprocess.run', side_effect=responses) as run, patch('agentops.github.time.sleep'):
            self.assertEqual(gh_transport('GET', 'repos/o/r/pulls/1'), {'ok': True})
        self.assertEqual(run.call_count, 2)

    def test_read_only_internal_server_error_is_retried_without_a_model(self):
        responses = [SimpleNamespace(returncode=1, stderr='gh: HTTP 500', stdout=''),
                     SimpleNamespace(returncode=0, stderr='', stdout='{"ok":true}')]
        with patch('agentops.github.subprocess.run', side_effect=responses) as run, patch('agentops.github.time.sleep'):
            self.assertEqual(gh_transport('GET', 'repos/o/r/pulls/1'), {'ok': True})
        self.assertEqual(run.call_count, 2)

    def test_server_error_after_write_is_never_retried_blindly(self):
        response = SimpleNamespace(returncode=1, stderr='gh: HTTP 500', stdout='')
        with patch('agentops.github.subprocess.run', return_value=response) as run:
            with self.assertRaises(RuntimeError):
                gh_transport('POST', 'repos/o/r/issues/1/comments', {'body': 'review'})
        self.assertEqual(run.call_count, 1)
