"""Read-only local file navigation for benchmark pickers; never read contents."""

import os
from pathlib import Path

_KINDS = {
    "wheel": {".whl"},
    "json": {".json"},
    "dataset": {".json", ".jsonl"},
    "ssh": None,
}


def list_files(location="", kind="json"):
    if kind not in _KINDS:
        raise ValueError("Unknown file picker type")
    if not isinstance(location, str) or len(location) > 8192 or "\0" in location:
        raise ValueError("Invalid directory")
    try:
        directory = (
            Path(location).expanduser().resolve(strict=True)
            if location
            else Path(__file__).resolve().parents[1]
        )
        if directory.is_file():
            directory = directory.parent
        if not directory.is_dir():
            raise ValueError("Choose an accessible directory")
        entries = []
        for item in directory.iterdir():
            try:
                is_directory = item.is_dir()
                if is_directory or (
                    item.is_file()
                    and (_KINDS[kind] is None or item.suffix.lower() in _KINDS[kind])
                ):
                    entries.append(
                        {
                            "name": item.name,
                            "path": str(item),
                            "directory": is_directory,
                        }
                    )
            except OSError:
                continue
        entries.sort(
            key=lambda entry: (not entry["directory"], entry["name"].casefold())
        )
    except (OSError, RuntimeError, ValueError):
        raise ValueError("Directory unavailable. Choose another location.") from None
    roots = [
        {"name": "Home", "path": str(Path.home())},
        {"name": "AgentOps", "path": str(Path(__file__).resolve().parents[1])},
    ]
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        roots.append({"name": "Downloads", "path": str(downloads)})
    if os.name == "nt":
        import ctypes

        drives = ctypes.windll.kernel32.GetLogicalDrives()
        roots += [
            {"name": f"{chr(65 + i)}:", "path": f"{chr(65 + i)}:/"}
            for i in range(26)
            if drives & (1 << i)
        ]
    else:
        roots.append({"name": "Filesystem", "path": "/"})
    return {
        "directory": str(directory),
        "parent": str(directory.parent) if directory.parent != directory else None,
        "roots": roots,
        "entries": entries,
        "kind": kind,
    }
