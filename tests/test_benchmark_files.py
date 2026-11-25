import tempfile
import unittest
from pathlib import Path

from agentops.benchmark_files import list_files


class BenchmarkFilePickerTests(unittest.TestCase):
    def test_filters_files_but_preserves_directory_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "folder").mkdir()
            (root / "candidate.WHL").write_bytes(b"wheel")
            (root / "runner.json").write_text("{}")
            (root / "samples.jsonl").write_text("{}")
            (root / "private.txt").write_text("secret contents")
            result = list_files(str(root), "wheel")
            self.assertEqual(
                {e["name"] for e in result["entries"]}, {"folder", "candidate.WHL"}
            )
            self.assertEqual(result["directory"], str(root.resolve()))
            self.assertEqual(
                {e["name"] for e in list_files(str(root), "json")["entries"]},
                {"folder", "runner.json"},
            )
            dataset = list_files(str(root), "dataset")
            self.assertEqual(
                {e["name"] for e in dataset["entries"]},
                {"folder", "runner.json", "samples.jsonl"},
            )
            self.assertNotIn("secret contents", str(list_files(str(root), "ssh")))
            self.assertEqual(
                list_files(str(root / "runner.json"), "json")["directory"],
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


if __name__ == "__main__":
    unittest.main()
