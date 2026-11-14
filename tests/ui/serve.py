"""Isolated browser-test fixture: no watcher, GitHub calls or real workers."""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentops.cli import DEFAULTS
from agentops.ledger import Ledger
from agentops.panel import make_server


with tempfile.TemporaryDirectory(prefix='agentops-ui-') as directory:
    path = Path(directory) / 'ledger.sqlite3'
    ledger = Ledger(path)
    body = '# Summary\n\n**Safe** Markdown with `inline code`.\n\n' + '\n\n'.join(
        f'## Section {i}\n\nDescription paragraph {i}.' for i in range(30))
    prs = [{'number': i, 'title': f'Test pull request {i}', 'author': 'contributor',
            'head_sha': 'a' * 40, 'head_ref': 'feature/test', 'base_ref': 'main',
            'body': body, 'draft': False} for i in range(1, 31)]
    snapshot = ledger.save_snapshot({'repo': 'example/repository', 'prs': prs})
    run = ledger.create_run(snapshot, {'harness': 'codex'})
    for pr in prs:
        ledger.enqueue(run, pr, 'b' * 40)
    config = {**DEFAULTS, 'repo': 'example/repository', 'state': directory, 'publish': False}
    with make_server(path, port=18766, config=config) as server:
        server.serve_forever()
