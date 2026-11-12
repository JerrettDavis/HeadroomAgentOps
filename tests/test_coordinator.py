import json
import tempfile
import unittest
from pathlib import Path

from agentops.coordinator import Coordinator
from agentops.ledger import Ledger
from agentops.evidence import Evidence


class FakeGitHub:
    repo = 'o/r'
    def __init__(self):
        self.main = 'a' * 40

    def main_sha(self):
        return self.main

    def pull(self, number):
        return {'number': number, 'head_sha': 'b' * 40, 'base_sha': self.main, 'base_ref': 'main',
                'author': 'author', 'title': 'PR', 'draft': True, 'head_ref': 'fix', 'head_repo': 'o/r',
                'state': 'open', 'mergeable': 'MERGEABLE', 'body': 'intent', 'files': [],
                'threads': [], 'reviews': [], 'comments': [], 'review_comments': [],
                'checks': [], 'workflows': [], 'statuses': [], 'discussion_hash': 'd', 'checks_hash': 'c'}


class FakeWorker:
    def __init__(self, root):
        self.root = root
        self.active = set()
        self.max_active = 0
        self.sessions = []
        self.cleaned = set()

    def prepare(self, job, pr, evidence, packet_text):
        import uuid
        session = str(uuid.uuid4())
        path = self.root / session
        (path / 'input').mkdir(parents=True)
        (path / 'output').mkdir()
        (path / 'input' / 'packet.json').write_text(packet_text, encoding='utf8')
        self.sessions.append((job['number'], session))
        return {'session_id': session, 'directory': str(path), 'assignment': {**job, 'session_id': session}}

    def start(self, prepared):
        self.active.add(prepared['session_id'])
        self.max_active = max(self.max_active, len(self.active))
        return {**prepared, 'container': prepared['session_id']}

    def observe(self, handle):
        return {'state': 'exited', 'exit_code': 0}

    def collect(self, handle, job):
        self.active.discard(handle['session_id'])
        return {'schema': 1, 'session_id': handle['session_id'], 'number': job['number'],
                'head_sha': job['head_sha'], 'main_sha': job['main_sha'], 'outcome': 'held',
                'summary': 'Missing hosted evidence.', 'findings': [], 'actions': [],
                'evidence': [], 'notices': [], 'coverage': {'files': [], 'threads': []}}

    def cancel(self, handle):
        self.active.discard(handle['session_id'])

    def cleanup(self, handle):
        self.cleaned.add(handle['session_id'])


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Ledger(self.root / 'ledger.db')
        self.github = FakeGitHub()
        self.worker = FakeWorker(self.root / 'jobs')
        snapshot = {'repo': 'o/r', 'main_sha': 'a' * 40, 'prs': [self.github.pull(n) for n in range(1, 6)]}
        self.snapshot = self.db.save_snapshot(snapshot)
        self.run = self.db.create_run(self.snapshot, {'concurrency': 2})
        for pr in snapshot['prs']:
            self.db.enqueue(self.run, pr, snapshot['main_sha'])
        self.coordinator = Coordinator(self.db, self.github, self.worker, Evidence(self.root / 'evidence'),
                                       concurrency=2, poll_seconds=0)

    def test_five_prs_use_five_fresh_sessions_and_bounded_parallelism(self):
        status = self.coordinator.run(self.run)
        self.assertEqual(status['states'], {'held': 5})
        self.assertEqual(len(self.worker.sessions), 5)
        self.assertEqual(len({session for number, session in self.worker.sessions}), 5)
        self.assertLessEqual(self.worker.max_active, 2)
        self.assertGreater(self.worker.max_active, 1)

    def test_second_unchanged_pass_reuses_verified_evidence_instead_of_starting_more_agents(self):
        self.coordinator.run(self.run)
        second_run = self.db.create_run(self.snapshot, {'concurrency': 2})
        for number in range(1, 6):
            self.db.enqueue(second_run, self.github.pull(number), 'a' * 40)
        self.coordinator.run(second_run)
        self.assertEqual(len(self.worker.sessions), 5)
        self.assertTrue(all(job['result'].get('reused_from') for job in self.db.jobs(second_run)))

    def test_unchanged_undelivered_feedback_is_retained_without_repeating_reasoning(self):
        collect = self.worker.collect
        def with_feedback(handle, job):
            value = collect(handle, job)
            value['actions'] = [{'kind': 'comment', 'body': 'A verified unresolved question.',
                                 'event': '', 'candidate_sha': '', 'workflow_id': 0}]
            return value
        self.worker.collect = with_feedback
        self.coordinator.run(self.run)
        second_run = self.db.create_run(self.snapshot, {'concurrency': 2})
        for number in range(1, 6):
            self.db.enqueue(second_run, self.github.pull(number), 'a' * 40)
        self.coordinator.run(second_run)
        self.assertEqual(len(self.worker.sessions), 5)
        self.assertTrue(all(job['result']['actions'] for job in self.db.jobs(second_run)))

    def test_unchanged_needs_followup_result_keeps_its_hold_without_new_reasoning(self):
        collect = self.worker.collect
        def unresolved(handle, job):
            value = collect(handle, job)
            value['outcome'] = 'needs_followup'
            value['findings'] = [{'severity': 'question', 'path': 'a.py', 'line': 1,
                                 'description': 'Author intent is unresolved.', 'evidence': 'Recorded discussion.'}]
            return value
        self.worker.collect = unresolved
        self.coordinator.run(self.run)
        second = self.db.create_run(self.snapshot, {})
        for number in range(1, 6):
            self.db.enqueue(second, self.github.pull(number), 'a' * 40)
        self.coordinator.run(second)
        self.assertEqual(len(self.worker.sessions), 5)
        self.assertTrue(all(j['state'] == 'needs_followup' and
            j['result']['publication_qualification'] == 'needs_followup' for j in self.db.jobs(second)))

    def test_old_proof_with_unreviewed_late_notice_cannot_be_reused(self):
        self.coordinator.run(self.run)
        first = self.db.jobs(self.run)[0]
        notice = self.db.notice('o/r', 'learning', 'New evidence after dispatch', {'source': 'fixture'}, [first['number']])
        value = {**first['result'], 'outcome': 'needs_followup', 'reviewed_notice_ids': [notice],
                 'qualification_limits': ['New relevant shared evidence requires review']}
        value['evidence_record'] = self.coordinator.evidence.put(value)
        self.db.reclassify(first['id'], 'needs_followup', value)
        second = self.db.create_run(self.snapshot, {})
        self.db.enqueue(second, self.github.pull(first['number']), 'a' * 40)
        self.coordinator.run(second)
        self.assertEqual(len(self.worker.sessions), 6)

    def test_notice_arriving_during_collection_is_not_relabelled_as_reviewed(self):
        collect = self.worker.collect
        def late_notice(handle, job):
            value = collect(handle, job)
            self.db.notice('o/r', 'learning', 'New evidence', {'job': job['id']}, [job['number']])
            return value
        self.worker.collect = late_notice
        self.coordinator.run(self.run)
        for job in self.db.jobs(self.run):
            self.assertEqual(job['state'], 'needs_followup')
            self.assertEqual(job['result']['reviewed_notice_ids'], [])

    def test_worker_authored_suggestion_is_known_and_does_not_cause_another_session(self):
        from types import SimpleNamespace
        collect = self.worker.collect
        def suggestion(handle, job):
            value = collect(handle, job)
            value['notices'] = [{'kind': 'learning', 'body': 'A scoped observation.',
                                 'evidence': 'source:fixture', 'scope': 'pr'}]
            return value
        def publish(job, original, value):
            current = {n['id'] for n in self.db.notices('o/r', job['number'])}
            self.assertLessEqual(current, set(value['reviewed_notice_ids']))
            return {'sent': [], 'held': [], 'own_discussion_entries': []}
        self.worker.collect = suggestion
        self.coordinator.publisher = SimpleNamespace(publish=publish)
        self.assertEqual(self.coordinator.run(self.run)['states'], {'held': 5})
        second = self.db.create_run(self.snapshot, {})
        for number in range(1, 6):
            self.db.enqueue(second, self.github.pull(number), 'a' * 40)
        self.coordinator.run(second)
        self.assertEqual(len(self.worker.sessions), 5)

    def test_undelivered_candidate_archive_keeps_original_identity_across_reuse_hops(self):
        collect = self.worker.collect
        cleanup = self.worker.cleanup
        def with_candidate(handle, job):
            value = collect(handle, job)
            value['actions'] = [{'kind': 'push', 'candidate_sha': 'c' * 40,
                                 'body': '', 'event': '', 'workflow_id': 0}]
            return value
        self.worker.collect = with_candidate
        self.worker.archive_candidate = lambda handle, job, evidence: evidence.put({
            'job_id': job['id'], 'session_id': handle['session_id'],
            'head_sha': job['head_sha'], 'main_sha': job['main_sha'], 'bundle_base64': 'fixture'})
        self.worker.cleanup = lambda handle, **kwargs: cleanup(handle)
        self.coordinator.run(self.run)
        originals = {job['number']: job['id'] for job in self.db.jobs(self.run)}
        runs = []
        for _ in range(2):
            run = self.db.create_run(self.snapshot, {})
            runs.append(run)
            for number in range(1, 6):
                self.db.enqueue(run, self.github.pull(number), 'a' * 40)
            self.coordinator.run(run)
            for job in self.db.jobs(run):
                self.assertEqual(job['result']['candidate_archive_source_job'], originals[job['number']])
        self.assertEqual(len(self.worker.sessions), 5)
        changed = self.db.jobs(runs[-1])[0]
        altered = {**changed['result'], 'candidate_archive_source_job': 'unrelated-source'}
        altered['evidence_record'] = self.coordinator.evidence.put(altered)
        self.db.reclassify(changed['id'], changed['state'], altered)
        fourth = self.db.create_run(self.snapshot, {})
        self.db.enqueue(fourth, self.github.pull(changed['number']), 'a' * 40)
        self.coordinator.run(fourth)
        recovered = self.db.jobs(fourth)[0]['result']
        self.assertNotEqual(recovered['reused_from'], changed['id'])
        self.assertEqual(recovered['candidate_archive_source_job'], originals[changed['number']])
        self.assertEqual(len(self.worker.sessions), 5)

    def test_pending_ci_metadata_change_reuses_proof_without_new_reasoning(self):
        self.coordinator.run(self.run)
        original = self.github.pull
        self.github.pull = lambda number: {**original(number), 'checks_hash': 'progress-only'}
        second_run = self.db.create_run(self.snapshot, {'concurrency': 2})
        for number in range(1, 6):
            self.db.enqueue(second_run, self.github.pull(number), 'a' * 40)
        self.coordinator.run(second_run)
        self.assertEqual(len(self.worker.sessions), 5)

    def test_new_terminal_ci_failure_requires_new_reasoning(self):
        self.coordinator.run(self.run)
        original = self.github.pull
        self.github.pull = lambda number: {**original(number), 'checks_hash': 'new-failure',
            'checks': [{'id': 99, 'head_sha': 'b' * 40, 'status': 'completed', 'conclusion': 'failure'}]}
        second_run = self.db.create_run(self.snapshot, {'concurrency': 2})
        for number in range(1, 6):
            self.db.enqueue(second_run, self.github.pull(number), 'a' * 40)
        self.coordinator.run(second_run)
        self.assertEqual(len(self.worker.sessions), 10)

    def test_reused_awaiting_ci_proof_can_be_qualified_mechanically_when_ci_is_green(self):
        import copy
        from test_publisher import pr, result
        from agentops.model import REVIEW_POLICY_VERSION
        prior = self.db.claim('previous', 2, self.run)
        current = {**pr(), 'number': prior['number'], 'head_sha': prior['head_sha']}
        for check in current['checks'] + current['workflows']:
            check['head_sha'] = prior['head_sha']
        original = copy.deepcopy(current)
        original['checks_hash'] = 'pending'
        original['workflows'][0].update(status='in_progress', conclusion=None)
        proof = {**result(), 'outcome': 'awaiting_ci', 'review_policy_version': REVIEW_POLICY_VERSION,
                 'reviewed_notice_ids': [], 'reviewed_pr_evidence': self.coordinator.evidence.put(original)}
        proof['evidence_record'] = self.coordinator.evidence.put(proof)
        self.db.finish(prior['id'], 'previous', 'awaiting_ci', proof)
        second = self.db.create_run(self.snapshot, {})
        self.db.enqueue(second, current, 'a' * 40)
        job = self.db.claim(self.coordinator.owner, 2, second)
        state, reused = self.coordinator._reuse(job, current, [])
        self.assertEqual(state, 'ready')
        self.assertEqual(reused['outcome'], 'ready')
        self.assertEqual(reused['worker_outcome'], 'awaiting_ci')
        self.db.finish(job['id'], self.coordinator.owner, state, reused)
        third = self.db.create_run(self.snapshot, {})
        self.db.enqueue(third, current, 'a' * 40)
        third_job = self.db.claim(self.coordinator.owner, 2, third)
        _, reused_again = self.coordinator._reuse(third_job, current, [])
        self.assertEqual(reused_again['worker_outcome'], 'awaiting_ci')
        self.db.finish(third_job['id'], self.coordinator.owner, 'needs_followup', reused_again)
        fourth = self.db.create_run(self.snapshot, {})
        self.db.enqueue(fourth, current, 'a' * 40)
        fourth_job = self.db.claim(self.coordinator.owner, 2, fourth)
        state, still_held = self.coordinator._reuse(fourth_job, current, [])
        self.assertEqual(state, 'needs_followup')
        self.assertEqual(still_held['publication_qualification'], 'needs_followup')

    def test_changed_author_evidence_prevents_reuse(self):
        self.coordinator.run(self.run)
        original = self.github.pull
        self.github.pull = lambda number: {**original(number), 'discussion_hash': 'new-author-evidence'}
        second_run = self.db.create_run(self.snapshot, {'concurrency': 2})
        for number in range(1, 6):
            self.db.enqueue(second_run, self.github.pull(number), 'a' * 40)
        self.coordinator.run(second_run)
        self.assertEqual(len(self.worker.sessions), 10)

    def test_force_fresh_bypasses_unchanged_evidence(self):
        self.coordinator.run(self.run)
        second_run = self.db.create_run(self.snapshot, {'concurrency': 2})
        for number in range(1, 6):
            self.db.enqueue(second_run, self.github.pull(number), 'a' * 40)
        self.coordinator.force_fresh = True
        self.coordinator.run(second_run)
        self.assertEqual(len(self.worker.sessions), 10)

    def test_main_change_marks_entire_plan_stale_before_dispatch(self):
        self.github.main = 'c' * 40
        status = self.coordinator.run(self.run)
        self.assertEqual(status['states'], {'stale': 5})
        self.assertEqual(self.worker.sessions, [])

    def test_stopped_run_does_not_dispatch(self):
        self.db.stop(self.run)
        self.coordinator.run(self.run)
        self.assertEqual(self.worker.sessions, [])

    def test_orphaned_worker_is_adopted_without_starting_a_duplicate_session(self):
        from agentops.process import process_identity
        owner = json.dumps({**process_identity(), 'started': 'dead-owner'})
        job = self.db.claim(owner, 2, self.run)
        original = self.github.pull(job['number'])
        from agentops.planning import packet
        prepared = self.worker.prepare(job, original, self.coordinator.evidence,
            packet(original, self.db.get_snapshot(self.snapshot), [], self.coordinator.evidence))
        handle = self.worker.start(prepared)
        self.db.attach_handle(job['id'], owner, handle)
        self.coordinator.run(self.run)
        self.assertEqual(len(self.worker.sessions), 5)
        self.assertEqual(self.db.job(job['id'])['result']['session_id'], handle['session_id'])


    def test_worker_failure_is_visible_and_does_not_stop_other_prs(self):
        original = self.worker.collect
        def collect(handle, job):
            if job['number'] == 1:
                self.worker.active.discard(handle['session_id'])
                raise ValueError('invalid structured output')
            return original(handle, job)
        self.worker.collect = collect
        status = self.coordinator.run(self.run)
        self.assertEqual(status['states'], {'failed': 1, 'held': 4})
        self.assertIn('invalid structured output', self.db.jobs(self.run)[0]['result']['error'])
        self.assertEqual(len(self.worker.cleaned), 5)

    def test_failed_worker_without_export_retains_checkout_but_retires_auth(self):
        calls = []
        self.worker.archive_candidate = lambda *args: None
        self.worker.cleanup = lambda handle, preserve_checkout=False: calls.append(preserve_checkout)
        result = {}
        self.coordinator._cleanup_terminal({'directory': 'owned-checkout'}, {}, result, True)
        self.assertEqual(calls, [True])
        self.assertEqual(result['recovery_checkout_retained'], 'owned-checkout')

    def test_failed_worker_with_verified_archive_can_clean_checkout(self):
        calls = []
        reference = {'sha256': 'a' * 64}
        self.worker.archive_candidate = lambda *args: reference
        self.worker.cleanup = lambda handle, preserve_checkout=False: calls.append(preserve_checkout)
        result = {}
        self.coordinator._cleanup_terminal({'directory': 'owned-checkout'}, {}, result, True)
        self.assertEqual(calls, [False])
        self.assertEqual(result['candidate_archive'], reference)


if __name__ == '__main__':
    unittest.main()
