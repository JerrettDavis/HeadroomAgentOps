"""Content-addressed JSON evidence with integrity checks."""

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from .model import canonical


class Evidence:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data):
        content = canonical(data).encode('utf8')
        identity = hashlib.sha256(content).hexdigest()
        relative = f'{identity}.json'
        target = self.root / relative
        temporary = self.root / f'.{uuid.uuid4().hex}.tmp'
        try:
            temporary.write_bytes(content)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {'sha256': identity, 'path': relative, 'bytes': len(content)}

    def get(self, identity):
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('Invalid evidence hash')
        target = self.root / f'{identity}.json'
        if target.resolve().parent != self.root:
            raise ValueError('Evidence path escapes root')
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != identity:
            raise ValueError('Evidence hash mismatch')
        return json.loads(content)
