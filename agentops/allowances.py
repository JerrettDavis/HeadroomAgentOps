"""Shared, fail-closed admission gate for subscription-backed review workers."""

import json
import math
import time
import uuid

from .provider_usage import (PROVIDERS, credential_scope, authentication_fingerprint,
                             subscription_policy_error, number, read_usage)


FRESH_SECONDS = 60
REFRESH_LEASE_SECONDS = 60


def validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) - {'enabled', 'stop_percent', 'token_budget'}:
        raise ValueError('Unknown provider limit setting')
    if 'enabled' in settings and type(settings['enabled']) is not bool:
        raise ValueError('Limit enforcement must be true or false')
    if 'stop_percent' in settings:
        percent = settings['stop_percent']
        if type(percent) not in (int, float) or not math.isfinite(percent) or not 1 <= percent <= 100:
            raise ValueError('Stop point must be between 1 and 100 percent used')
    if 'token_budget' in settings:
        budget = settings['token_budget']
        if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
            raise ValueError('Token budget must be a positive number of tokens')


class Allowances:
    def __init__(self, ledger, config, reader=read_usage, clock=time.time,
                 identity_reader=authentication_fingerprint):
        self.ledger, self.config, self.reader, self.clock = ledger, config, reader, clock
        self.identity_reader = identity_reader
        self.provider = config.get('harness')
        if self.provider not in PROVIDERS:
            raise ValueError('Unknown allowance provider')
        self.settings_key = 'allowance.settings:' + self.provider

    @property
    def cache_key(self):
        return 'allowance.usage:' + credential_scope(self.config)

    @staticmethod
    def _get(db, key):
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else {}

    @staticmethod
    def _put(db, key, value):
        db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (key, json.dumps(value, allow_nan=False)))

    def _settings(self, db):
        configured = self.config.get('provider_limits', {}).get(self.provider, {})
        validate_settings(configured)
        settings = {'enabled': True, 'stop_percent': 80, **configured,
                    **self._get(db, self.settings_key)}
        validate_settings(settings)
        return settings

    def configure(self, request):
        validate_settings(request)
        if not request:
            raise ValueError('At least one provider limit setting is required')
        with self.ledger.connect(immediate=True) as db:
            self._put(db, self.settings_key, {**self._get(db, self.settings_key), **request})
        return self.status()

    def _fresh(self, cached, now):
        observed = cached.get('observed_at')
        if observed is None or not 0 <= now - observed < FRESH_SECONDS or cached.get('error'):
            return False
        return all(w['resets_at'] > now for w in cached.get('windows', []))

    def _status(self, settings, cached, now, admission=False):
        fresh = self._fresh(cached, now)
        status = {'provider': self.provider, **settings, 'allowed': False,
                  'state': 'unknown', 'reason': 'Allowance has not been checked; new reviews wait.',
                  'source': cached.get('source'), 'windows': cached.get('windows', []),
                  'observed_at': cached.get('observed_at'), 'checked_at': cached.get('checked_at'),
                  'fresh': fresh, 'refreshing': cached.get('refreshing_until', 0) > now,
                  'unlimited': cached.get('unlimited', False)}
        if admission:
            status['_auth_fingerprint'] = cached.get('auth_fingerprint')
        auth_error = subscription_policy_error(self.config)
        if auth_error:
            status.update(state='authentication', reason=auth_error)
        elif not settings['enabled']:
            status.update(allowed=True, state='disabled', reason='Allowance enforcement is off.')
        elif cached.get('error'):
            status.update(state='unavailable', reason='Account allowance unavailable; check provider sign-in. New reviews wait.')
        elif not fresh:
            if cached.get('observed_at') is not None:
                status.update(state='stale', reason='Allowance needs a fresh reading; new reviews wait.')
        elif not any(w['period'] == 'weekly' for w in cached.get('windows', [])):
            status.update(state='partial', reason='Weekly allowance is not reported by this source; new reviews wait while protection is enabled.')
        elif cached.get('blocked'):
            status.update(state='paused', reason='Provider reports an account limit; new reviews wait.')
        else:
            reached = [w for w in cached.get('windows', []) if w['used_percent'] >= settings['stop_percent']]
            if reached:
                status.update(state='paused', reason=f'{reached[0]["label"]} reached the {settings["stop_percent"]:g}% stop point. New reviews wait.')
            else:
                status.update(allowed=True, state='available', reason='Below the stop point; reviews can start.')
        return status

    def status(self):
        key, now = self.cache_key, self.clock()
        with self.ledger.connect() as db:
            return self._status(self._settings(db), self._get(db, key), now)

    def matches_prepared(self, prepared, admission=None):
        try:
            fingerprint = prepared.get('auth_fingerprint')
            return (bool(fingerprint) and fingerprint == self.identity_reader(self.config) and
                    (admission is None or not admission['enabled'] or
                     fingerprint == admission.get('_auth_fingerprint')))
        except Exception:
            return False

    @staticmethod
    def _normalize(value):
        """Whitelist persisted fields even for future/custom provider adapters."""
        windows = []
        for entry in value['windows']:
            windows.append({'id': str(entry['id'])[:160], 'label': str(entry['label'])[:160],
                            'period': entry['period'] if entry['period'] in ('weekly', 'short', 'monthly') else 'other',
                            'window_minutes': number(entry['window_minutes']),
                            'used_percent': number(entry['used_percent']),
                            'resets_at': number(entry['resets_at'])})
        unlimited = value.get('unlimited') is True
        if not windows and not unlimited:
            raise ValueError('No allowance observation')
        return {'source': str(value['source'])[:160], 'windows': windows,
                'blocked': value.get('blocked') is True, 'unlimited': unlimited,
                'auth_fingerprint': value.get('auth_fingerprint')}

    def _consumed_since(self, since):
        """Locally measured billed tokens for this provider, for consumption budgets."""
        total = 0
        with self.ledger.connect() as db:
            for (result,) in db.execute(
                    'SELECT result FROM jobs WHERE heartbeat >= ? AND result IS NOT NULL', (since,)):
                try:
                    execution = (json.loads(result) or {}).get('execution') or {}
                except ValueError:
                    continue
                if execution.get('harness') != self.provider:
                    continue
                usage = execution.get('usage') or {}
                total += int(usage.get('input_tokens') or 0) + int(usage.get('output_tokens') or 0)
        return total

    def check(self, refresh=True):
        """Refresh at most once per minute across coordinators, then apply current settings."""
        key, now = self.cache_key, self.clock()
        try:
            current_identity = self.identity_reader(self.config)
        except Exception:
            current_identity = 'unavailable'
        lease = uuid.uuid4().hex
        with self.ledger.connect(immediate=True) as db:
            cached = self._get(db, key)
            settings = self._settings(db)
            identity_matches = cached.get('auth_fingerprint') == current_identity
            if not identity_matches:
                cached = {**cached, 'error': True}
            if (not refresh or not settings['enabled'] or (identity_matches and self._fresh(cached, now)) or
                    cached.get('refreshing_until', 0) > now or
                    (cached.get('error') and cached.get('attempted_identity') == current_identity and
                     0 <= now - cached.get('checked_at', 0) < FRESH_SECONDS)):
                return self._status(settings, cached, now, admission=True)
            cached.update(refreshing_until=now + REFRESH_LEASE_SECONDS, refresh_owner=lease)
            self._put(db, key, cached)
        try:
            reading = self._normalize(self.reader(
                {**self.config, '_consumed_since': self._consumed_since}))
            if not reading['auth_fingerprint'] or reading['auth_fingerprint'] != current_identity:
                raise ValueError('Allowance account changed during collection')
            if any(w['resets_at'] <= self.clock() for w in reading['windows']):
                raise ValueError('Provider returned expired allowance')
            result = {**reading, 'observed_at': self.clock(), 'error': False}
        except Exception:
            # A provider exception can contain response bodies, URLs and headers.
            # Retain only last-known normalized usage and a generic failure bit.
            result = {**cached, 'error': True}
        with self.ledger.connect(immediate=True) as db:
            current = self._get(db, key)
            if current.get('refresh_owner') == lease:
                result.update(checked_at=self.clock(), refreshing_until=0, attempted_identity=current_identity)
                result.pop('refresh_owner', None)
                self._put(db, key, result)
                current = result
            # A threshold may have changed while the provider request was running.
            return self._status(self._settings(db), current, self.clock(), admission=True)
