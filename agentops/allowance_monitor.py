"""Responsive dashboard view of configured and recently used providers."""

import json
import threading

from .allowances import Allowances
from .ledger import Ledger
from .provider_usage import PROVIDERS, authentication_fingerprint, read_usage


class AllowanceMonitor:
    def __init__(self, path, config, reader=read_usage, identity_reader=authentication_fingerprint):
        self.path, self.config, self.reader = path, config, reader
        self._ledger = None
        self.identity_reader = identity_reader
        self.ledger_lock = threading.Lock()
        self.lock = threading.Lock()
        self.thread = None

    @property
    def ledger(self):
        # Merely constructing the panel must leave existing saved reports read-only.
        with self.ledger_lock:
            if self._ledger is None:
                self._ledger = Ledger(self.path)
            return self._ledger

    def managers(self):
        configs = {self.config['harness']: self.config}
        # The active configuration wins; retain the latest selection for each
        # other provider so manually launched harnesses are visible as well.
        with self.ledger.connect() as db:
            for row in db.execute('SELECT config FROM runs ORDER BY created DESC'):
                saved = json.loads(row[0])
                provider = saved.get('harness')
                if provider in PROVIDERS and provider not in configs:
                    configs[provider] = {**self.config, 'auth_root': None, **saved}
                if len(configs) == len(PROVIDERS):
                    break
        for provider in self.config.get('provider_limits', {}):
            if provider not in PROVIDERS:
                raise ValueError('Unknown configured allowance provider')
            configs.setdefault(provider, {**self.config, 'harness': provider, 'auth_root': None})
        return {p: Allowances(self.ledger, config, reader=self.reader,
                             identity_reader=self.identity_reader) for p, config in configs.items()}

    def status(self, refresh=False):
        managers = self.managers()
        result = {'providers': [manager.status() for manager in managers.values()],
                  'refresh_seconds': 60, 'in_flight_policy': 'finish'}
        if refresh:
            with self.lock:
                if not self.thread or not self.thread.is_alive():
                    self.thread = threading.Thread(target=self._refresh, args=(list(managers.values()),),
                                                   daemon=True, name='provider-allowances')
                    self.thread.start()
        return result

    @staticmethod
    def _refresh(managers):
        for manager in managers:
            manager.check()

    def configure(self, request):
        provider = request.get('provider')
        managers = self.managers()
        if provider not in managers:
            raise ValueError('Select a configured allowance provider')
        managers[provider].configure({k: v for k, v in request.items() if k != 'provider'})
        return self.status(refresh=True)
