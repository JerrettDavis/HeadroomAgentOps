"""Token-protected local file navigation using server-issued locations only."""

import os
import ntpath
import posixpath
import stat
import threading
from collections import OrderedDict
from pathlib import Path, PureWindowsPath, PurePosixPath

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

    def resolve_file(self, location, kind="wheel"):
        """Typed paths select actual entries under trusted local roots.

        Never resolve/stat caller text: Windows can authenticate to a network
        filesystem during resolution, before ordinary extension checks run.
        """
        if (kind not in _KINDS or not isinstance(location, str) or not location or
                len(location) > 8192 or '\x00' in location or location.startswith(('\\\\', '//'))):
            raise ValueError('Choose a regular file on a local drive')
        if len(location) > 1 and location[1] == ':' and location[2:3] not in ('\\', '/'):
            raise ValueError('Drive-relative paths are not supported')
        windows = os.name == 'nt'
        pure_type = PureWindowsPath if windows else PurePosixPath
        if location.startswith(('~/', '~\\')):
            location = str(Path.home()) + os.sep + location[2:]
        if not pure_type(location).is_absolute():
            location = str(Path.cwd()) + os.sep + location
        normalized = (ntpath.normpath if windows else posixpath.normpath)(location)
        pure = pure_type(normalized)
        if (not pure.is_absolute() or str(pure).startswith(('\\\\', '//')) or
                (windows and (len(pure.drive) != 2 or not pure.drive[0].isascii() or
                              not pure.drive[0].isalpha() or pure.drive[1] != ':')) or
                (_KINDS[kind] is not None and pure.suffix.lower() not in _KINDS[kind])):
            raise ValueError('Choose a supported local file')
        parts = pure.parts
        compare = (lambda part: part.casefold()) if windows else (lambda part: part)
        with self.lock:
            roots = [self.issued[r['path']] for r in self.roots if r['path'] in self.issued]
            matching = [root for root in roots if tuple(map(compare, parts[:len(root.parts)])) ==
                        tuple(map(compare, root.parts))]
            if not matching:
                raise ValueError('Location is not on a supported local drive')
            current = min(matching, key=lambda root: len(root.parts))
            if windows:
                import ctypes
                if ctypes.windll.kernel32.GetDriveTypeW(current.anchor) not in (2, 3, 5, 6):
                    raise ValueError('Network drives are not supported')
            remaining = parts[len(current.parts):]
            if not remaining:
                raise ValueError('Select a regular file')
            for index, component in enumerate(remaining):
                if component in ('.', '..') or (windows and (':' in component or
                        component.rstrip(' .').split('.')[0].upper() in
                        {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                         *(f'LPT{i}' for i in range(1, 10))})):
                    raise ValueError('Unsupported file path component')
                item = next((entry for entry in current.iterdir() if compare(entry.name) == compare(component)), None)
                if item is None:
                    raise ValueError('Selected file is unavailable')
                observed = item.lstat()
                if stat.S_ISLNK(observed.st_mode) or getattr(observed, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Linked and reparse locations are not supported')
                final = index == len(remaining) - 1
                if not (stat.S_ISREG(observed.st_mode) if final else stat.S_ISDIR(observed.st_mode)):
                    raise ValueError('Selected location is not a regular local file')
                current = item
            self.issued[str(current)] = current
            return current

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


def resolve_local_file(location, kind="wheel"):
    return _BROWSER.resolve_file(location, kind)
