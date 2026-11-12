import json
import tempfile
import unittest
import uuid
from pathlib import Path

from agentops.harness import Harness, validate_result, usage_from_events, response_from_events
from agentops.worker import Worker, container_command, safe_job_path, auth_plan, verify_copilot_scopes
from agentops.process import process_identity, owner_state


def result():
    return {'schema': 1, 'session_id': str(uuid.uuid4()), 'number': 1,
            'head_sha': 'b' * 40, 'main_sha': 'a' * 40, 'outcome': 'held',
            'summary': 'Missing live proof.', 'findings': [], 'evidence': [],
            'actions': [], 'notices': [], 'coverage': {'files': [], 'threads': []}}


class WorkerTests(unittest.TestCase):
    def test_missing_docker_object_is_recognized_independent_of_error_case(self):
        def runner(args, **kwargs):
            if args[:3] == ['docker', 'image', 'inspect']:
                return 'sha256:' + 'a' * 64
            raise RuntimeError('docker failed (1): error: no such object: owned-container')
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(directory, 'o/r', runner=runner)
            self.assertEqual(worker.observe({'container': 'owned-container', 'session_id': str(uuid.uuid4())}),
                             {'state': 'missing'})

    def test_copilot_final_message_is_parsed_and_later_non_json_cannot_reuse_older_output(self):
        expected = result()
        events = [{'type': 'assistant.message', 'data': {'content': json.dumps(expected)}},
                  {'type': 'result', 'exitCode': 0, 'usage': {}}]
        self.assertEqual(response_from_events('copilot', events), expected)
        events.append({'type': 'assistant.message', 'data': {'content': 'Review is incomplete.'}})
        with self.assertRaises(ValueError):
            response_from_events('copilot', events)
    def test_cleanup_requires_terminal_owned_container_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = str(uuid.uuid4())
            job = root / session
            for name in ('repo', 'input', 'output', 'auth'):
                (job / name).mkdir(parents=True)
            (job / 'repo' / 'source.txt').write_text('disposable clone')
            import stat
            (job / 'repo' / 'source.txt').chmod(stat.S_IREAD)
            (job / 'auth' / 'auth.json').write_text('model secret')
            (job / 'output' / 'result.json').write_text('{}')
            running = True
            def runner(args, **kwargs):
                if args[:2] == ['docker', 'inspect']:
                    return json.dumps([{'State': {'Running': running, 'ExitCode': 0},
                                        'Config': {'Labels': {'agentops.session': session}}}])
                return ''
            worker = Worker(root, 'o/r', runner=runner)
            handle = {'session_id': session, 'container': 'owned', 'directory': str(job)}
            with self.assertRaises(ValueError):
                worker.cleanup(handle)
            self.assertTrue((job / 'repo' / 'source.txt').is_file())
            running = False
            worker.cleanup(handle)
            self.assertFalse((job / 'repo').exists())
            self.assertEqual(list((job / 'auth').iterdir()), [])
            self.assertTrue((job / 'output' / 'result.json').is_file())

    def test_container_startup_failure_is_retained_before_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = str(uuid.uuid4())
            path = root / session
            (path / 'output').mkdir(parents=True)
            def runner(args, **kwargs):
                return 'Traceback: harness could not start'
            worker = Worker(root, 'o/r', runner=runner)
            diagnostic = worker.diagnostics({'session_id': session, 'container': 'owned', 'directory': str(path)})
            self.assertIn('harness could not start', diagnostic)
            self.assertIn('Traceback', (path / 'output' / 'container.log').read_text(encoding='utf8'))
    def test_live_coordinator_identity_is_not_treated_as_missing(self):
        identity = process_identity()
        self.assertEqual(owner_state(json.dumps(identity)), 'live')
        self.assertEqual(owner_state('legacy-unverifiable-owner'), 'unknown')
        self.assertEqual(owner_state(json.dumps({**identity, 'started': 'wrong'})), 'missing')
        self.assertEqual(owner_state(json.dumps({k: v for k, v in identity.items() if k != 'started'})), 'unknown')
    def test_actual_harness_usage_is_recorded_without_fabricating_missing_values(self):
        codex = [{'type': 'turn.completed', 'usage': {'input_tokens': 100, 'output_tokens': 20}},
                 {'type': 'turn.completed', 'usage': {'input_tokens': 80, 'output_tokens': 10}}]
        self.assertEqual(usage_from_events('codex', codex)['input_tokens'], 180)
        self.assertEqual(usage_from_events('codex', codex)['output_tokens'], 30)
        claude = [{'type': 'result', 'usage': {'input_tokens': 2, 'output_tokens': 30,
                   'cache_creation_input_tokens': 100}, 'total_cost_usd': 0.02}]
        self.assertEqual(usage_from_events('claude', claude)['cost_usd'], 0.02)
        self.assertIsNone(usage_from_events('codex', []))

    def test_result_must_bind_exact_session_and_revisions(self):
        value = result()
        job = {key: value[key] for key in ('session_id', 'number', 'head_sha', 'main_sha')}
        self.assertEqual(validate_result(value, job)['outcome'], 'held')
        for key in job:
            bad = {**value, key: 'wrong'}
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_result(bad, job)

    def test_unknown_actions_and_unsubstantiated_readiness_are_rejected(self):
        value = result()
        for change in ({'actions': [{'kind': 'merge'}]}, {'outcome': 'ready'},
                       {'findings': 'none'}, {'schema': 99}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_result({**value, **change}, value)

    def test_every_adapter_starts_noninteractive_fresh_session(self):
        for name in ('codex', 'claude', 'copilot'):
            first = {**result(), 'prompt': 'Review exactly this PR.'}
            second = {**first, 'session_id': str(uuid.uuid4())}
            one = Harness(name).command(first)
            two = Harness(name).command(second)
            self.assertFalse(any(a in ('resume', '--resume', '--continue') for a in one))
            self.assertTrue('exec' in one or '--print' in one or '--prompt' in one)
            if name != 'codex':
                self.assertNotEqual(one, two)
            self.assertNotIn('--fleet', one)

    def test_model_selection_is_passed_as_one_argument(self):
        command = Harness('codex', 'custom model').command({**result(), 'prompt': 'hi'})
        self.assertEqual(command[command.index('--model') + 1], 'custom model')
        with self.assertRaises(ValueError):
            Harness('unknown')

    def test_container_receives_only_job_owned_paths_and_no_host_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('repo', 'input', 'output', 'auth'):
                (root / name).mkdir()
            command = container_command(root, str(uuid.uuid4()), 'sha256:' + 'a' * 64)
            mounts = [command[i + 1] for i, a in enumerate(command) if a == '--mount']
            self.assertEqual(len(mounts), 4)
            self.assertTrue(all(f'src={root.resolve()}' in m for m in mounts))
            self.assertNotIn('/var/run/docker.sock', ' '.join(command))
            self.assertIn('--read-only', command)
            self.assertIn('no-new-privileges', command)
            self.assertIn('--cap-drop=ALL', command)
            self.assertNotIn('--privileged', command)

    def test_job_paths_reject_traversal_and_mount_delimiters(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                safe_job_path(directory, '../secret')
            with self.assertRaises(ValueError):
                safe_job_path(directory, 'bad,path')

    def test_auth_never_inherits_general_github_credentials(self):
        env = {'GH_TOKEN': 'publisher-secret', 'GITHUB_TOKEN': 'publisher-secret',
               'OPENAI_API_KEY': 'model-secret', 'ANTHROPIC_API_KEY': 'anthropic-secret'}
        auth = auth_plan('codex', env, None)
        self.assertEqual(auth['env'], {'OPENAI_API_KEY': 'model-secret'})
        self.assertNotIn('publisher-secret', json.dumps(auth))
        with self.assertRaisesRegex(ValueError, 'Copilot'):
            auth_plan('copilot', env, None)

    def test_copilot_token_requires_verified_model_only_scope(self):
        env = {'AGENTOPS_COPILOT_TOKEN': 'token'}
        with self.assertRaises(ValueError):
            auth_plan('copilot', env, None, copilot_scopes=['repo', 'copilot'])
        with self.assertRaises(ValueError):
            auth_plan('copilot', env, None, copilot_scopes=[])
        auth = auth_plan('copilot', env, None, copilot_scopes=['copilot'])
        self.assertEqual(auth['env'], {'COPILOT_GITHUB_TOKEN': 'token'})

    def test_copilot_scopes_are_inspected_without_forwarding_publisher_token(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        def limited(request, timeout):
            return nullcontext(SimpleNamespace(headers={'X-OAuth-Scopes': 'copilot, read:user'}))
        scopes = verify_copilot_scopes({'AGENTOPS_COPILOT_TOKEN': 'dedicated-model-token'}, opener=limited)
        self.assertEqual(scopes, ['copilot', 'read:user'])
        with self.assertRaises(ValueError):
            verify_copilot_scopes({'GH_TOKEN': 'publisher-token'}, opener=limited)
        def broad(request, timeout):
            return nullcontext(SimpleNamespace(headers={'X-OAuth-Scopes': 'repo, copilot'}))
        with self.assertRaises(ValueError):
            verify_copilot_scopes({'AGENTOPS_COPILOT_TOKEN': 'broad-token'}, opener=broad)

    def test_copilot_byok_forwards_only_model_provider_configuration(self):
        env = {'COPILOT_PROVIDER_BASE_URL': 'https://model.example.test/v1',
               'COPILOT_PROVIDER_TYPE': 'openai', 'COPILOT_PROVIDER_WIRE_API': 'responses',
               'COPILOT_PROVIDER_BEARER_TOKEN': 'model-only-bearer',
               'COPILOT_PROVIDER_MODEL_ID': 'gpt-6.1-sol',
               'COPILOT_PROVIDER_WIRE_MODEL': 'provider-alias',
               'COPILOT_PROVIDER_HEADERS': 'ChatGPT-Account-ID: 12345678-1234-1234-1234-123456789abc',
               'GH_TOKEN': 'publisher-secret', 'GITHUB_TOKEN': 'publisher-secret',
               'COPILOT_MODEL': 'configured-model'}
        auth = auth_plan('copilot', env, None)
        self.assertEqual(auth['env']['COPILOT_PROVIDER_BEARER_TOKEN'], 'model-only-bearer')
        self.assertEqual(auth['env']['COPILOT_MODEL'], 'configured-model')
        self.assertEqual(auth['env']['COPILOT_PROVIDER_WIRE_MODEL'], 'provider-alias')
        self.assertEqual(auth['env']['COPILOT_PROVIDER_HEADERS'], env['COPILOT_PROVIDER_HEADERS'])
        self.assertNotIn('publisher-secret', json.dumps(auth))
        with self.assertRaises(ValueError):
            auth_plan('copilot', {**env, 'COPILOT_PROVIDER_API_KEY_COMMAND': 'read-host-secret'}, None)
        with self.assertRaises(ValueError):
            auth_plan('copilot', {**env, 'COPILOT_PROVIDER_HEADERS': 'Authorization: publisher-token'}, None)

    def test_subscription_auth_only_copies_named_auth_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'auth.json').write_text('{"tokens":{"access_token":"model-only"}}')
            (root / 'config.toml').write_text('dangerous inherited MCP configs')
            plan = auth_plan('codex', {}, root)
            self.assertEqual(plan['files'], {'auth.json': str(root / 'auth.json')})


if __name__ == '__main__':
    unittest.main()
