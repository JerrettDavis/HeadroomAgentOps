import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agentops.process import command
from agentops.github import gh_transport


class BackgroundProcessTests(unittest.TestCase):
    def test_command_disables_console_creation(self):
        with patch('agentops.process.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='ok', stderr='')) as run:
            self.assertEqual(command(['example']), 'ok')
        self.assertEqual(run.call_args.kwargs.get('creationflags'), getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def test_github_disables_console_creation(self):
        with patch('agentops.github.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='{}', stderr='')) as run:
            self.assertEqual(gh_transport('GET', 'user'), {})
        self.assertEqual(run.call_args.kwargs.get('creationflags'), getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    @unittest.skipUnless(os.name == 'nt', 'Windows console behavior')
    def test_command_child_has_no_console(self):
        self.assertEqual(command([sys.executable, '-c', 'import ctypes; print(bool(ctypes.windll.kernel32.GetConsoleWindow()))']), 'False')
