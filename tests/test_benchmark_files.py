import tempfile
import unittest
from pathlib import Path

from agentops.benchmark_files import FileBrowser, list_files


class BenchmarkFilePickerTests(unittest.TestCase):
    def test_typed_local_wheel_walks_trusted_directories_without_resolving_request_text(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            nested = root / 'nested'
            nested.mkdir()
            wheel = nested / 'candidate.whl'
            wheel.write_bytes(b'fixture')
            browser = FileBrowser([root])
            with patch.object(Path, 'resolve', side_effect=AssertionError('request resolution is forbidden')):
                self.assertEqual(browser.resolve_file(str(wheel), 'wheel'), wheel)
                for location in (r'\\external\share\candidate.whl', '//external/share/candidate.whl',
                                 r'\\?\C:\candidate.whl', r'\\.\device\candidate.whl', 'C:relative.whl'):
                    with self.assertRaises(ValueError):
                        browser.resolve_file(location, 'wheel')

    def test_filters_files_but_preserves_directory_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            browser = FileBrowser([root])
            (root / "folder").mkdir()
            (root / "candidate.WHL").write_bytes(b"wheel")
            (root / "runner.json").write_text("{}")
            (root / "samples.jsonl").write_text("{}")
            (root / "private.txt").write_text("secret contents")
            result = browser.list(str(root), "wheel")
            self.assertEqual(
                {e["name"] for e in result["entries"]}, {"folder", "candidate.WHL"}
            )
            self.assertEqual(result["directory"], str(root.resolve()))
            self.assertEqual(
                {e["name"] for e in browser.list(str(root), "json")["entries"]},
                {"folder", "runner.json"},
            )
            dataset = browser.list(str(root), "dataset")
            self.assertEqual(
                {e["name"] for e in dataset["entries"]},
                {"folder", "runner.json", "samples.jsonl"},
            )
            self.assertNotIn("secret contents", str(browser.list(str(root), "ssh")))
            self.assertEqual(
                browser.list(str(root / "runner.json"), "json")["directory"],
                str(root.resolve()),
            )
            self.assertTrue(
                all(set(e) == {"name", "path", "directory"} for e in result["entries"])
            )

    def test_invalid_inputs_do_not_execute_or_return_file_contents(self):
        for path, kind in (
            ("", "unknown"),
            ("missing-directory", "wheel"),
            ("\0", "json"),
        ):
            with self.assertRaises(ValueError):
                list_files(path, kind)
        self.assertTrue(list_files("", "wheel")["roots"])

    def test_only_server_issued_locations_can_be_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            folder = root / "nested"
            folder.mkdir()
            browser = FileBrowser([root])
            # Even existing locations cannot be injected before the server has
            # offered them through directory navigation.
            with self.assertRaises(ValueError):
                browser.list(str(folder), "json")
            browser.list(str(root), "json")
            self.assertEqual(
                browser.list(str(folder), "json")["directory"], str(folder)
            )
            for location in (
                "../",
                str(root / ".."),
                "//external/share",
                r"\\external\share",
            ):
                with self.assertRaises(ValueError):
                    browser.list(location, "json")


if __name__ == "__main__":
    unittest.main()
