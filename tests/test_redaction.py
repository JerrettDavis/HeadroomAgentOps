import json
import tempfile
import unittest
from pathlib import Path

from agentops.redaction import collect_secrets, redact


class RedactionTests(unittest.TestCase):
    def test_model_tokens_are_removed_from_retained_output(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / 'auth.json'
            auth.write_text(json.dumps({'tokens': {'access_token': 'private-model-token-value'},
                                        'auth_mode': 'chatgpt'}))
            secrets = collect_secrets([auth])
            output = redact('tool stdout: private-model-token-value; ordinary finding', secrets)
            self.assertNotIn('private-model-token-value', output)
            self.assertIn('ordinary finding', output)
            self.assertNotIn('chatgpt', secrets)

    def test_public_feedback_redacts_recognizable_credential_formats(self):
        output = redact('Found ghp_' + 'A' * 36 + ' in a fixture; remove it.')
        self.assertNotIn('A' * 36, output)
        self.assertIn('remove it', output)


if __name__ == '__main__':
    unittest.main()
