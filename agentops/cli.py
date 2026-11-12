"""Scriptable interface. Mechanical inventory never calls a model."""

import argparse
import contextlib
import json
import os
import sys
import tomllib
from pathlib import Path

from .coordinator import Coordinator
from .candidates import CandidatePublisher
from .ci import CIHandler
from .evidence import Evidence
from .github import GitHub
from .ledger import Ledger
from .model import canonical
from .planning import plan
from .process import command
from .publisher import Publisher
from .reconcile import reconcile_jobs
from .recovery import recover_job
from .worker import Worker, auth_plan, verify_copilot_scopes
from .copilot_relay import NativeRelay, native_credential, retire_relays


DEFAULTS = {'repo': 'headroomlabs-ai/headroom', 'state': '.agentops', 'concurrency': 2,
            'harness': 'codex', 'model': None, 'image': 'headroom-agentops-worker:local',
            'auth_root': None, 'publish': False, 'timeout_seconds': 3600,
            'packet_budget': 24576, 'poll_seconds': 5, 'copilot_native': False,
            'copilot_account': None, 'copilot_cli_root': None}


def load_config(args):
    config = dict(DEFAULTS)
    source = Path(args.config)
    if source.exists():
        config.update(tomllib.loads(source.read_text(encoding='utf8')))
    elif args.config != 'agentops.toml':
        raise ValueError(f'Configuration does not exist: {source}')
    for key in DEFAULTS:
        value = getattr(args, key, None)
        if value is not None:
            config[key] = value
    if type(config['concurrency']) is not int or not 1 <= config['concurrency'] <= 4:
        raise ValueError('Concurrency must be an integer from 1 to 4')
    if config['harness'] not in ('codex', 'claude', 'copilot'):
        raise ValueError('Unknown harness')
    for field in ('timeout_seconds', 'packet_budget', 'poll_seconds'):
        if type(config[field]) not in (int, float) or config[field] <= 0:
            raise ValueError(f'{field} must be positive')
    if not config['auth_root']:
        if config['harness'] == 'codex':
            config['auth_root'] = os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))
        elif config['harness'] == 'claude':
            config['auth_root'] = str(Path.home() / '.claude')
    return config


def parser():
    cli = argparse.ArgumentParser(description='Bounded, fresh-session Headroom PR review flow')
    cli.add_argument('--config', default='agentops.toml')
    cli.add_argument('--state')
    sub = cli.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Inspect installed tools, isolated image and model authentication')
    panel = sub.add_parser('panel', help='Serve the local read-only review dashboard')
    panel.add_argument('--host', default='127.0.0.1', choices=['127.0.0.1', 'localhost', '::1'])
    panel.add_argument('--port', type=int, default=8765)
    inventory = sub.add_parser('plan', help='Read-only inventory, sort and persist an immutable plan')
    inventory.add_argument('--snapshot', help='Import a complete saved inventory for replay')
    inventory.add_argument('--pr', type=int, action='append')
    inventory.add_argument('--limit', type=int)
    run = sub.add_parser('run', help='Run a persisted plan, each PR in its own fresh container/session')
    run.add_argument('--plan', dest='run_id')
    run.add_argument('--pr', type=int, action='append')
    run.add_argument('--limit', type=int)
    run.add_argument('--harness', choices=['codex', 'claude', 'copilot'])
    run.add_argument('--model')
    run.add_argument('--auth-root')
    run.add_argument('--concurrency', type=int)
    run.add_argument('--publish', action=argparse.BooleanOptionalAction, default=None)
    run.add_argument('--force-fresh', action='store_true', help='Bypass verified prior review reuse')
    run.add_argument('--copilot-native', action=argparse.BooleanOptionalAction, default=None,
                     help='Use local Copilot sign-in through a model-only loopback relay')
    run.add_argument('--copilot-account')
    run.add_argument('--copilot-cli-root')
    status = sub.add_parser('status', help='Read the local ledger without contacting GitHub')
    status.add_argument('--run', dest='run_id')
    status.add_argument('--details', action='store_true')
    stop = sub.add_parser('stop', help='Stop new dispatch; live coordinators cancel owned workers')
    stop.add_argument('run_id')
    retry = sub.add_parser('retry', help='Explicit bounded follow-up in a fresh session')
    retry.add_argument('job_id')
    retry.add_argument('--reason', required=True)
    recover = sub.add_parser('recover', help='Requalify completed output after a GitHub read failure; no new model session')
    recover.add_argument('job_id')
    recover.add_argument('--publish', action=argparse.BooleanOptionalAction, default=None)
    reconcile = sub.add_parser('reconcile', help='Refresh queue, invalidate stale evidence and mechanically recheck CI')
    reconcile.add_argument('--run', dest='run_id')
    reconcile.add_argument('--retry-changed', action='store_true')
    notices = sub.add_parser('notices', help='Inspect or record scoped shared knowledge')
    notices.add_argument('--pr', required=True, type=int)
    notices.add_argument('--body')
    notices.add_argument('--kind', default='suggestion', choices=['suggestion', 'learning', 'security', 'policy'])
    notices.add_argument('--source', help='Required provenance for a new notice')
    evidence = sub.add_parser('evidence', help='Retrieve and verify a full content-addressed artifact')
    evidence.add_argument('sha256')
    return cli


def make_plan(config, args, ledger, github, evidence):
    if args.snapshot:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding='utf8'))
    else:
        snapshot = github.snapshot()
    if snapshot.get('complete') is not True or not isinstance(snapshot.get('prs'), list):
        raise ValueError('A complete paginated inventory is required')
    if snapshot.get('repo') != config['repo']:
        raise ValueError('Snapshot repository does not match the configured target repository')
    identity = ledger.save_snapshot(snapshot)
    evidence.put(snapshot)
    prior = {j['number']: j for j in ledger.jobs() if j['repo'] == snapshot['repo']}
    selected = plan(snapshot, prior)
    if args.pr:
        requested = set(args.pr)
        selected = [p for p in selected if p['number'] in requested]
        missing = requested - {p['number'] for p in selected}
        if missing:
            raise ValueError(f'Requested PRs absent from open queue: {sorted(missing)}')
    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError('Plan limit must be positive')
        selected = selected[:args.limit]
    stored_config = {**config, 'repo': snapshot['repo']}
    run_id = ledger.create_run(identity, stored_config)
    for pr in selected:
        ledger.enqueue(run_id, pr, snapshot['main_sha'])
    return {'run_id': run_id, 'snapshot_id': identity, 'main_sha': snapshot['main_sha'],
            'queue_total': len(snapshot['prs']), 'selected': [p['number'] for p in selected],
            'harness': config['harness'], 'concurrency': config['concurrency'], 'publish': config['publish']}


def main(argv=None):
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(effective_argv or ['run'])
    resources = contextlib.ExitStack()
    try:
        config = load_config(args)
        root = Path(config['state']).resolve()
        if args.command == 'panel':
            from .panel import serve
            serve(root / 'ledger.sqlite3', args.host, args.port)
            return 0
        ledger, evidence = Ledger(root / 'ledger.sqlite3'), Evidence(root / 'evidence')
        github = GitHub(config['repo'])
        if args.command == 'status':
            result = ledger.status(args.run_id)
            if args.details:
                result['jobs'] = ledger.jobs(args.run_id)
        elif args.command == 'plan':
            result = make_plan(config, args, ledger, github, evidence)
        elif args.command == 'run':
            if not args.run_id:
                created = make_plan(config, argparse.Namespace(snapshot=None, pr=args.pr, limit=args.limit),
                                    ledger, github, evidence)
                args.run_id = created['run_id']
                print(canonical({'event': 'planned', **created}), file=sys.stderr, flush=True)
            saved = ledger.get_run(args.run_id)['config']
            # Runtime selections are explicit overrides; other saved plan choices remain reproducible.
            effective = {**config, **saved, 'state': str(root)}
            for key in ('harness', 'model', 'auth_root', 'concurrency', 'publish',
                        'copilot_native', 'copilot_account', 'copilot_cli_root'):
                value = getattr(args, key, None)
                if value is not None:
                    effective[key] = value
            if args.harness and args.harness != saved['harness'] and args.auth_root is None:
                effective['auth_root'] = load_config(args)['auth_root']
            config = effective
            github = GitHub(config['repo'])
            model_env, model_relay = None, None
            if config['harness'] == 'copilot' and config['copilot_native']:
                config['model'] = config['model'] or 'gpt-6.1-sol'
                relay = NativeRelay(root / 'relays', args.run_id, config['model'], config['copilot_account'],
                                    config['copilot_cli_root'], config['timeout_seconds']).__enter__()
                def close_relay():
                    try:
                        status = ledger.status(args.run_id)
                        preserve = status['live'] > 0
                    except Exception:
                        preserve, status = True, {}
                    relay.close(preserve=preserve)
                    if not preserve and not status.get('states', {}).get('queued'):
                        retire_relays(root / 'relays', args.run_id)
                resources.callback(close_relay)
                model_env, model_relay = relay.worker_environment(), relay.descriptor()
            auth_env = os.environ if model_env is None else model_env
            scopes = (verify_copilot_scopes(auth_env) if config['harness'] == 'copilot' and
                      not auth_env.get('COPILOT_PROVIDER_BASE_URL') else None)
            auth_plan(config['harness'], auth_env, config['auth_root'], scopes)
            worker = Worker(root / 'jobs', config['repo'], config['harness'], config['model'],
                            config['image'], config['auth_root'], copilot_scopes=scopes,
                            model_env=model_env, model_relay=model_relay)
            publisher = Publisher(github, ledger, CandidatePublisher(root / 'publisher', github, evidence=evidence),
                                  CIHandler(github)) if config['publish'] else None
            coordinator = Coordinator(ledger, github, worker, evidence, config['concurrency'],
                config['poll_seconds'], config['timeout_seconds'], config['packet_budget'], publisher,
                emit=lambda event: print(canonical(event), file=sys.stderr, flush=True),
                force_fresh=args.force_fresh)
            result = coordinator.run(args.run_id)
        elif args.command == 'recover':
            worker = Worker(root / 'jobs', config['repo'], image=config['image'])
            publisher = Publisher(github, ledger, CandidatePublisher(root / 'publisher', github, evidence=evidence),
                                  CIHandler(github)) if config['publish'] else None
            result = recover_job(ledger, github, evidence, worker, args.job_id, publisher)
        elif args.command == 'stop':
            ledger.stop(args.run_id)
            result = {'run_id': args.run_id, 'stopped': True,
                      'note': 'Live coordinators will stop dispatch and cancel their owned workers'}
        elif args.command == 'retry':
            ledger.retry(args.job_id)
            job = ledger.job(args.job_id)
            ledger.notice(job['repo'], 'learning', 'Follow-up requested: ' + args.reason,
                          {'job': args.job_id, 'source': 'explicit CLI retry'}, [job['number']])
            result = {'job_id': args.job_id, 'state': 'queued', 'reason': args.reason}
        elif args.command == 'reconcile':
            snapshot = github.snapshot()
            identity = ledger.save_snapshot(snapshot)
            ledger.invalidate(config['repo'], snapshot['main_sha'], {p['number']: p['head_sha'] for p in snapshot['prs']})
            reconciliation = reconcile_jobs(ledger, github, evidence, args.run_id, args.retry_changed)
            result = {'snapshot_id': identity, 'reconciliation': reconciliation, **ledger.status(args.run_id)}
        elif args.command == 'notices':
            if args.body:
                if not args.source:
                    raise ValueError('New notices require --source provenance')
                ledger.notice(config['repo'], args.kind, args.body, {'source': args.source}, [args.pr])
            result = ledger.notices(config['repo'], args.pr)
        elif args.command == 'evidence':
            result = evidence.get(args.sha256)
        else:
            checks = {}
            for name, invocation in [('git', ['git', '--version']), ('github', ['gh', 'api', 'user', '--jq', '.login']),
                                     ('docker', ['docker', 'info', '--format', '{{.ServerVersion}}']),
                                     ('image', ['docker', 'image', 'inspect', config['image'], '--format', '{{.Id}}'])]:
                try:
                    checks[name] = {'ok': True, 'value': command(invocation, timeout=30)}
                except Exception as exc:
                    checks[name] = {'ok': False, 'error': str(exc)}
            try:
                if config['harness'] == 'copilot' and config['copilot_native']:
                    native_credential(config['copilot_cli_root'], config['copilot_account'])
                    auth = {'files': {}, 'env': {}, 'mode': 'native model-only relay'}
                else:
                    scopes = (verify_copilot_scopes(os.environ) if config['harness'] == 'copilot' and
                          not os.environ.get('COPILOT_PROVIDER_BASE_URL') else None)
                    auth = auth_plan(config['harness'], os.environ, config['auth_root'], scopes)
                checks['model_auth'] = {'ok': True, 'files': list(auth['files']), 'env_names': list(auth['env'])}
            except ValueError as exc:
                checks['model_auth'] = {'ok': False, 'error': str(exc)}
            result = {'harness': config['harness'], 'checks': checks}
            print(json.dumps(result, ensure_ascii=False))
            return 0 if all(c['ok'] for c in checks.values()) else 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f'agentops: {exc}', file=sys.stderr)
        return 2
    finally:
        resources.close()


if __name__ == '__main__':
    sys.exit(main())
