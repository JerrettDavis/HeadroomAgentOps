"""Canonical data identities shared by inventory, ledger and publisher."""

import hashlib
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


TERMINAL = frozenset({'held', 'ready', 'failed', 'cancelled', 'stale',
                      'interrupted', 'needs_followup', 'awaiting_ci', 'reviewed'})
REVIEW_POLICY_VERSION = 2
