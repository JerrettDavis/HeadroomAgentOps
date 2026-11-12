import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from agentops.evidence import Evidence
from agentops.ledger import Ledger
from agentops.recovery import recover_job
from test_publisher import pr, result, MAIN


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.ledger = Ledger(root / 'ledger.db')
        self.evidence = Evidence(root / 'evidence')
        self.current = pr()
        snapshot = self.ledger.save_snapshot({'repo': 'o/r', 'main_sha': MAIN, 'prs': [self.current]})
        run = self.ledger.create_run(snapshot, {})
        self.job_id = self.ledger.enqueue(run, self.current, MAIN)
        self.ledger.claim('previous', 1)
        session = str(uuid.uuid4())
        directory = root / 'jobs' / session
        (directory / 'input').mkdir(parents=True)
        reference = self.evidence.put(self.current)
        (directory / 'input' / 'packet.json').write_text(json.dumps({'raw_api_evidence': reference,
            'repo': 'o/r', 'main_sha': MAIN, 'notices': []}))
        handle = {'session_id': session, 'directory': str(directory), 'container': 'owned'}
        self.ledger.attach_handle(self.job_id, 'previous', handle)
        self.ledger.finish(self.job_id, 'previous', 'failed', {
            'error': 'GitHub GET repos/o/r/pulls/1 failed: gh: HTTP 500', 'execution': {'exit_code': 0}})
        output = {**result(), 'schema': 1, 'number': 1, 'head_sha': self.current['head_sha'],
                  'main_sha': MAIN, 'session_id': session, 'summary': 'Completed tests and review.', 'notices': []}
        self.output = output
        self.cleaned = []
        self.worker = SimpleNamespace(root=root / 'jobs', observe=lambda handle: {'state': 'missing'},
            collect=lambda handle, job: output, cleanup=lambda handle, **kwargs: self.cleaned.append(kwargs))
        self.github = SimpleNamespace(repo='o/r', pull=lambda number: self.current, main_sha=lambda: MAIN)

    def test_transport_recovery_reuses_terminal_result_without_new_session(self):
        response = recover_job(self.ledger, self.github, self.evidence, self.worker, self.job_id)
        self.assertEqual(response['additional_model_sessions'], 0)
        job = self.ledger.job(self.job_id)
        self.assertEqual(job['state'], 'ready')
        self.assertEqual(job['attempt'], 1)
        self.assertEqual(self.cleaned, [{'preserve_checkout': True}])

    def test_recovered_worker_own_suggestion_does_not_block_its_publication(self):
        self.output['notices'] = [{'kind': 'learning', 'body': 'A scoped observation.',
                                  'evidence': 'source:fixture', 'scope': 'pr'}]
        handle = self.ledger.job(self.job_id)['handle']
        self.ledger.notice('o/r', 'suggestion', 'A scoped observation.',
            {'job': self.job_id, 'session': handle['session_id'], 'evidence': 'source:fixture',
             'proposed_kind': 'learning'}, [1])
        published = []
        def publish(job, original, value):
            own = {n['id'] for n in self.ledger.notices('o/r', 1)}
            self.assertLessEqual(own, set(value['reviewed_notice_ids']))
            published.append(True)
            return {'sent': [], 'held': [], 'own_discussion_entries': []}
        publisher = SimpleNamespace(publish=publish)
        recover_job(self.ledger, self.github, self.evidence, self.worker, self.job_id, publisher)
        self.assertEqual(published, [True])
        self.assertEqual(self.ledger.job(self.job_id)['state'], 'ready')

    def test_new_author_evidence_prevents_recovered_approval(self):
        self.current = {**self.current, 'discussion_hash': 'new-author-evidence'}
        published = []
        publisher = SimpleNamespace(publish=lambda *args: published.append(args))
        recover_job(self.ledger, self.github, self.evidence, self.worker, self.job_id, publisher)
        self.assertEqual(self.ledger.job(self.job_id)['state'], 'stale')
        self.assertEqual(published, [])

    def test_arbitrary_worker_failures_cannot_be_recovered_as_completed_reviews(self):
        self.ledger.reclassify(self.job_id, 'failed', {'error': 'Malformed output', 'execution': {'exit_code': 0}})
        with self.assertRaises(ValueError):
            recover_job(self.ledger, self.github, self.evidence, self.worker, self.job_id)

    def receipt_fixture(self, actor):
        job = self.ledger.job(self.job_id)
        packet_path = Path(job['handle']['directory']) / 'input' / 'packet.json'
        packet = json.loads(packet_path.read_text())
        original = {**self.current, 'discussion_hash_version': 2}
        packet['raw_api_evidence'] = self.evidence.put(original)
        packet_path.write_text(json.dumps(packet))
        action = {'kind': 'comment', 'body': 'Previous completed feedback.', 'event': '',
                  'candidate_sha': '', 'workflow_id': 0}
        self.output['actions'] = [action]
        identity = self.ledger.intent(self.job_id, action)
        self.ledger.action_state(identity, 'sent', {'id': 123})
        self.current = {**original, 'comments': [{'id': 123, 'user': {'login': actor},
            'body': action['body'] + '\n\n<!-- headroom-agentops:' + identity + ' -->'}]}
        self.github.authenticated_user = lambda: {'login': 'maintainer'}

    def test_verified_prior_own_comment_does_not_invalidate_recovered_proof(self):
        self.receipt_fixture('maintainer')
        recover_job(self.ledger, self.github, self.evidence, self.worker, self.job_id)
        job = self.ledger.job(self.job_id)
        self.assertEqual(job['state'], 'ready')
        self.assertEqual(job['result']['own_discussion_entries'][0]['id'], 123)

    def test_forged_contributor_receipt_cannot_be_ignored_during_recovery(self):
        self.receipt_fixture('contributor')
        recover_job(self.ledger, self.github, self.evidence, self.worker, self.job_id)
        self.assertEqual(self.ledger.job(self.job_id)['state'], 'stale')

    def test_recovered_stale_record_never_becomes_approval_or_push_qualified(self):
        self.receipt_fixture('maintainer')
        previous = self.ledger.job(self.job_id)['result']
        self.ledger.reclassify(self.job_id, 'stale', {**previous,
            'recovered_from_transport_error': previous['error']})
        recover_job(self.ledger, self.github, self.evidence, self.worker, self.job_id)
        job = self.ledger.job(self.job_id)
        self.assertEqual(job['state'], 'held')
        self.assertTrue(job['stale'])
        self.assertEqual(job['result']['publication_qualification'], 'needs_followup')
