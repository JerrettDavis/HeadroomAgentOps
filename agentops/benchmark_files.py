"""Token-protected local file navigation using server-issued locations only."""

import os
import threading
from collections import OrderedDict
from pathlib import Path

_KINDS = {
    "wheel": {".whl"},
    "json": {".json"},
    "dataset": {".json", ".jsonl"},
    "ssh": None,
}


def _roots():
    roots = [Path.home(), Path(__file__).resolve().parents[1]]
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        roots.append(downloads)
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        drives = kernel.GetLogicalDrives()
        roots.extend(
            Path(f"{chr(65 + i)}:/")
            for i in range(26)
            if drives & (1 << i)
            and kernel.GetDriveTypeW(f"{chr(65 + i)}:/") in (2, 3, 5, 6)
        )
    else:
        roots.append(Path("/"))
    return roots


class FileBrowser:
    """Requests select a previously issued Path instead of constructing one."""

    def __init__(self, roots=None):
        self.lock = threading.Lock()
        self.issued = OrderedDict()
        self.roots = []
        for path in _roots() if roots is None else roots:
            if str(path).startswith(("\\\\", "//")):
                continue
            try:
                path = path.resolve(strict=True)
                if not path.is_dir():
                    continue
            except OSError:
                continue
            self.roots.append({"name": path.name or str(path), "path": str(path)})
            self.issued[str(path)] = path
        source = Path(__file__).resolve().parents[1]
        if roots is None:
            for path in (
                source / "benchmarks",
                source / "benchmarks/tasksets",
                source / "benchmarks/policies",
            ):
                if path.is_dir():
                    self.issued[str(path)] = path
                    for item in path.glob("*.json"):
                        if not item.is_symlink():
                            self.issued[str(item)] = item
        self.default = (
            source
            if str(source) in self.issued
            else next(iter(self.issued.values()), None)
        )

    def list(self, location="", kind="json"):
        if kind not in _KINDS or not isinstance(location, str) or len(location) > 8192:
            raise ValueError("Invalid file picker request")
        with self.lock:
            # Values originate from trusted roots and directory entries, never
            # from Path(request), expanduser or resolve on request text.
            directory = self.issued.get(location) if location else self.default
            if directory is None:
                raise ValueError("Choose a folder offered by the file picker.")
            try:
                if directory.is_symlink() or directory.is_junction():
                    raise ValueError(
                        "Linked locations are not available in the picker."
                    )
                if directory.is_file():
                    directory = directory.parent
                entries = []
                for item in directory.iterdir():
                    try:
                        if item.is_symlink() or item.is_junction():
                            continue
                        is_directory = item.is_dir()
                        if is_directory or (
                            item.is_file()
                            and (
                                _KINDS[kind] is None
                                or item.suffix.lower() in _KINDS[kind]
                            )
                        ):
                            entries.append(
                                {
                                    "name": item.name,
                                    "path": str(item),
                                    "directory": is_directory,
                                }
                            )
                            self.issued[str(item)] = item
                    except OSError:
                        continue
                parent = directory.parent if directory.parent != directory else None
                if parent is not None:
                    self.issued[str(parent)] = parent
                protected = {r["path"] for r in self.roots} | {
                    str(self.default),
                    str(directory),
                }
                while len(self.issued) > 10000:
                    key, value = self.issued.popitem(last=False)
                    if key in protected:
                        self.issued[key] = value
            except (OSError, RuntimeError):
                raise ValueError(
                    "Directory unavailable. Choose another location."
                ) from None
        entries.sort(
            key=lambda entry: (not entry["directory"], entry["name"].casefold())
        )
        return {
            "directory": str(directory),
            "parent": str(parent) if parent else None,
            "roots": self.roots,
            "entries": entries,
            "kind": kind,
        }


_BROWSER = FileBrowser()


def list_files(location="", kind="json"):
    return _BROWSER.list(location, kind)
