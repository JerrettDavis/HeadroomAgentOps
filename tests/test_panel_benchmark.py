import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentops.panel import make_server


class FakeBenchmarks:
    def __init__(self):
        self.calls = []

    def status(self):
        return {"jobs": [], "defaults": {}, "active": False}

    def submit(self, payload):
        self.calls.append(payload)
        return {"id": "a" * 32, "state": "queued", "action": payload["action"]}

    def cancel(self, payload):
        self.calls.append(payload)
        return {"id": payload["id"], "state": "cancelled"}


class BenchmarkPanelTests(unittest.TestCase):
    def test_benchmark_routes_use_launch_token_and_serve_complete_view(self):
        with tempfile.TemporaryDirectory() as directory:
            service = FakeBenchmarks()
            server = make_server(
                Path(directory) / "ledger.sqlite3", port=0, benchmark_service=service
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/") as response:
                    html = response.read().decode()
                    self.assertIn('id="view-benchmarks"', html)
                    self.assertIn("/ui/benchmarking.js", html)
                    self.assertIn("/ui/benchmarking.css", html)
                for route, mime in (
                    ("/ui/benchmarking.js", "text/javascript"),
                    ("/ui/benchmarking.css", "text/css"),
                ):
                    with urlopen(base + route) as response:
                        self.assertTrue(
                            response.headers["Content-Type"].startswith(mime)
                        )
                        self.assertIn(
                            "style-src 'self'",
                            response.headers["Content-Security-Policy"],
                        )
                with urlopen(base + "/api/benchmarks") as response:
                    self.assertEqual(json.load(response)["jobs"], [])
                self.assertEqual(service.calls, [])
                payload = json.dumps(
                    {"action": "inspect", "artifact": "candidate.whl"}
                ).encode()
                with self.assertRaises(HTTPError) as missing_token:
                    urlopen(Request(base + "/api/benchmarks", data=payload))
                self.assertEqual(missing_token.exception.code, 403)
                with urlopen(base + "/api/launcher") as response:
                    token = json.load(response)["token"]
                headers = {
                    "Origin": base,
                    "X-Launch-Token": token,
                    "Content-Type": "application/json",
                }
                with self.assertRaises(HTTPError) as forbidden_browse:
                    urlopen(base + "/api/benchmark-files?kind=wheel")
                self.assertEqual(forbidden_browse.exception.code, 403)
                with urlopen(
                    Request(base + "/api/benchmark-files?kind=wheel", headers=headers)
                ) as response:
                    listing = json.load(response)
                    self.assertTrue(listing["roots"])
                    self.assertTrue(
                        all(
                            e["directory"] or e["name"].lower().endswith(".whl")
                            for e in listing["entries"]
                        )
                    )
                with urlopen(
                    Request(base + "/api/benchmarks", data=payload, headers=headers)
                ) as response:
                    self.assertEqual(json.load(response)["state"], "queued")
                cancel = json.dumps({"id": "a" * 32}).encode()
                with urlopen(
                    Request(
                        base + "/api/benchmarks/cancel", data=cancel, headers=headers
                    )
                ) as response:
                    self.assertEqual(json.load(response)["state"], "cancelled")
                self.assertEqual(len(service.calls), 2)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
