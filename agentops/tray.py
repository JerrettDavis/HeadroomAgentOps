"""Optional Windows notification-area companion; no third-party GUI dependency."""

import logging
import os
from pathlib import Path
import subprocess
import sys


def start_tray(host, port, panel_pid=None):
    if sys.platform != 'win32':
        return None
    address = f'[{host}]' if ':' in host else host
    try:
        return subprocess.Popen([
            'powershell.exe', '-NoProfile', '-STA', '-WindowStyle', 'Hidden',
            '-File', str(Path(__file__).with_name('tray.ps1')),
            '-DashboardUrl', f'http://{address}:{port}',
            '-PanelProcessId', str(panel_pid or os.getpid()),
        ], creationflags=0x08000000, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        logging.getLogger(__name__).warning('Tray unavailable; dashboard is still running', exc_info=True)
        return None
