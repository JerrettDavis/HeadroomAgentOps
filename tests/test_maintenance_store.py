import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class MaintenanceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.sqlite'

    def store(self, repo='owner/repo'):
        from agentops.maintenance_store import MaintenanceStore
        return MaintenanceStore(self.path, repo)

    def test_settings_persist_and_repository_isolated(self):
        store = self.store()
        self.assertFalse(store.settings()['autofix'])
        store.configure({'autofix': True})
        self.assertTrue(self.store().settings()['autofix'])
        self.assertFalse(self.store('other/repo').settings()['autofix'])
        with self.assertRaises(ValueError):
            store.configure({'autofix': 'true'})

    def test_claim_is_atomic_and_results_survive_reopen(self):
        store = self.store()
        with ThreadPoolExecutor(6) as pool:
            claimed = list(pool.map(lambda _: store.claim('update', '1', {'state': 'queued'}), range(12)))
        self.assertEqual(sum(claimed), 1)
        store.update('update', '1', {'state': 'updated', 'new_head': 'abc'})
        self.assertEqual(self.store().get('update', '1')['state'], 'updated')
        self.assertEqual(len(store.list('update')), 1)
        self.assertIsNone(self.store('other/repo').get('update', '1'))

    def test_put_updates_without_losing_identity(self):
        store = self.store()
        store.put('incident', 'sha', {'state': 'broken'})
        store.update('incident', 'sha', {'diagnosis': 'failure'})
        self.assertEqual(store.get('incident', 'sha')['state'], 'broken')
        self.assertEqual(store.list('incident')[0]['key'], 'sha')


if __name__ == '__main__':
    unittest.main()
