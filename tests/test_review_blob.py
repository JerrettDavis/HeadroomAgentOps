import base64
import hashlib
import unittest
from unittest.mock import Mock

from agentops.github import GitHub


class ReviewBlobTests(unittest.TestCase):
    def test_base_content_pins_base_commit_and_uses_verified_blob(self):
        github = GitHub('o/r', runner=Mock(side_effect=[
            {'merge_base_commit': {'sha': 'c' * 40}}, {'type': 'file', 'sha': 'a' * 40}]))
        github.review_blob = Mock(return_value='source')
        self.assertEqual(github.review_base_content({'base_sha': 'b' * 40, 'head_sha': 'd' * 40},
                                                   {'filename': 'space here.py'}), 'source')
        self.assertEqual(github.runner.call_args_list[0].args,
                         ('GET', 'repos/o/r/compare/' + 'b' * 40 + '...' + 'd' * 40, None))
        self.assertEqual(github.runner.call_args_list[1].args, ('GET',
            'repos/o/r/contents/space%20here.py?ref=' + 'c' * 40, None))
        github.review_blob.assert_called_once_with('a' * 40)

    def test_reads_and_caches_hash_verified_blob(self):
        data = b'print("hello")\n'
        sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        runner = Mock(return_value={'sha': sha, 'encoding': 'base64',
                                    'content': base64.b64encode(data).decode()})
        github = GitHub('o/r', runner=runner)
        self.assertEqual(github.review_blob(sha), data.decode())
        self.assertEqual(github.review_blob(sha), data.decode())
        runner.assert_called_once_with('GET', f'repos/o/r/git/blobs/{sha}', None)

    def test_mismatched_content_rejected(self):
        github = GitHub('o/r', runner=Mock(return_value={
            'sha': 'a' * 40, 'encoding': 'base64', 'content': 'YWJj'}))
        with self.assertRaises(ValueError):
            github.review_blob('a' * 40)
