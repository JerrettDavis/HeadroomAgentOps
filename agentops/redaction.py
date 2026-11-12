"""Keep known model credentials out of logs and public review text."""

import base64
import json
import re
from pathlib import Path


def collect_secrets(paths):
    secrets = set()
    def walk(value, key=''):
        if isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, child_key)
        elif isinstance(value, list):
            for child in value:
                walk(child, key)
        elif isinstance(value, str) and len(value) >= 8 and any(
                marker in key.lower() for marker in ('token', 'api_key', 'apikey', 'secret', 'password', 'account_id')):
            secrets.add(value)
        elif key == 'COPILOT_PROVIDER_HEADERS' and isinstance(value, str) and ':' in value:
            secrets.add(value.split(':', 1)[1].strip())
    for path in paths:
        try:
            walk(json.loads(Path(path).read_text(encoding='utf8')))
        except (OSError, ValueError):
            continue
    return secrets


def redact(text, secrets=()):
    variants = set()
    for secret in secrets:
        variants.update((secret, json.dumps(secret)[1:-1], base64.b64encode(secret.encode()).decode()))
    for value in sorted(variants, key=len, reverse=True):
        text = text.replace(value, '[REDACTED]')
    return re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,})',
                  '[REDACTED]', text)
