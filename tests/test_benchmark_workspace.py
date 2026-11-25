import tempfile
from pathlib import Path

from agentops.benchmarks.workspace import Workspace


def test_workspace_retries_transient_windows_file_locks(tmp_path, monkeypatch):
    original = tempfile.TemporaryDirectory.cleanup
    calls = []

    def flaky(self):
        calls.append(self.name)
        if len(calls) < 3:
            raise PermissionError("native module handle is still closing")
        return original(self)

    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", flaky)
    monkeypatch.setattr("agentops.benchmarks.workspace.time.sleep", lambda value: None)
    with Workspace(prefix="candidate-", dir=tmp_path) as name:
        path = Path(name)
        (path / "owned.txt").write_text("owned")
    assert len(calls) == 3
    assert not path.exists()
