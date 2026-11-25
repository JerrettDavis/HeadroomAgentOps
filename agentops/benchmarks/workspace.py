"""Owned temporary workspaces with bounded retries for Windows file locks."""

import tempfile
import time
from pathlib import Path


class Workspace(tempfile.TemporaryDirectory):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._owned_parent = Path(self.name).parent.resolve()
        self._owned_name = Path(self.name).name

    def cleanup(self):
        # Closing a Windows process job is synchronous for process membership,
        # but native image/filesystem handles may remain unavailable briefly.
        # Never let a candidate redirect cleanup outside this owned directory.
        for attempt in range(10):
            target = Path(self.name).resolve()
            if target.parent != self._owned_parent or target.name != self._owned_name:
                raise ValueError("temporary workspace cleanup target changed")
            try:
                return super().cleanup()
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(min(0.05 * 2**attempt, 1))
