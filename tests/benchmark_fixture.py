"""Build an explicit simulator wheel without package indexes or model calls."""

import hashlib
from pathlib import Path
from zipfile import ZipFile

SERVER = """
import argparse, json, os, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
p = argparse.ArgumentParser()
p.add_argument('--host')
p.add_argument('--port', type=int)
p.add_argument('--no-optimize', action='store_true')
p.add_argument('--fixture-delay', type=float, default=0)
args, _ = p.parse_known_args()
enabled = not args.no_optimize and os.environ.get('HEADROOM_OPTIMIZE') != '0'
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def respond(self, data):
        payload = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def do_GET(self):
        if self.path == '/health':
            self.respond({'config': {'optimize': enabled}})
        else:
            self.respond({'rollout': {'registry_digest': 'sha256:' + 'c' * 64,
                                     'snapshot_digest': 'sha256:' + 'd' * 64,
                                     'qualification_eligible': True, 'unsafe_override': False}})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if args.fixture_delay:
            Path('request-started').write_text(str(os.getpid()))
            time.sleep(args.fixture_delay)
        tokens = 70 if enabled else 100
        self.respond({'model': body['model'], 'choices': [{'message': {'content': 'hello'}}],
                      'usage': {'prompt_tokens': tokens}})
HTTPServer((args.host, args.port), Handler).serve_forever()
"""


def build_fixture(directory):
    path = Path(directory) / "headroom_benchmark_fixture-0.0.0-py3-none-any.whl"
    info = "headroom_benchmark_fixture-0.0.0.dist-info/"
    with ZipFile(path, "w") as wheel:
        for name in ("headroom/__init__.py", "headroom/proxy/__init__.py"):
            wheel.writestr(name, "")
        wheel.writestr("headroom/proxy/server.py", SERVER)
        wheel.writestr(
            info + "METADATA",
            "Metadata-Version: 2.1\nName: headroom-benchmark-fixture\nVersion: 0.0.0\nProvides-Extra: proxy\n",
        )
        wheel.writestr(
            info + "WHEEL",
            "Wheel-Version: 1.0\nGenerator: benchmark-simulator\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr(info + "RECORD", "")
    return path, "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
