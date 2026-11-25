import unittest

from agentops.material_diff import compare_review_diff


def pr(patch, path='app.py', **file_fields):
    return {'files': [{'filename': path, 'status': 'modified', 'patch': patch, **file_fields}]}


class MaterialDiffTests(unittest.TestCase):
    def test_main_update_changes_hunk_location_only(self):
        old = pr('@@ -1 +1 @@\n-old()\n+new()', base_content='old()\n')
        new = pr('@@ -8 +8 @@\n-old()\n+new()', base_content='header\n' * 7 + 'old()\n')
        self.assertFalse(compare_review_diff(old, new)['material'])

    def test_moved_edit_with_identical_context_requires_base_alignment(self):
        old = pr('@@ -1 +1 @@\n-old()\n+new()')
        new = pr('@@ -8 +8 @@\n-old()\n+new()')
        self.assertTrue(compare_review_diff(old, new)['material'])

    def test_dependency_only_update(self):
        self.assertFalse(compare_review_diff(pr('-a\n+b', 'uv.lock'),
                                            pr('-a\n+c', 'uv.lock'))['material'])

    def test_trailing_whitespace_only(self):
        self.assertFalse(compare_review_diff(
            pr('-old()\n+new()', content='new()\n'),
            pr('-old()\n+new()  ', content='new()  \n'))['material'])

    def test_patch_line_could_be_inside_multiline_string(self):
        self.assertTrue(compare_review_diff(pr('-old()\n+new()'),
                                            pr('-old()\n+new()  '))['material'])

    def test_full_source_string_whitespace_is_material(self):
        self.assertTrue(compare_review_diff(
            pr('-old()\n+new()', content='x = """\nnew()\n"""'),
            pr('-old()\n+new()  ', content='x = """\nnew()  \n"""'))['material'])

    def test_logical_newline_change_is_material(self):
        old = 'def f():\n    return\n    (1)\n'
        new = 'def f():\n    return (\n    1)\n'
        self.assertTrue(compare_review_diff(pr('-a\n+b', content=old),
                                            pr('-a\n+c', content=new))['material'])

    def test_edit_moved_between_functions_is_material(self):
        old = '@@ -1 +1 @@ def first():\n-old()\n+new()'
        new = '@@ -9 +9 @@ def second():\n-old()\n+new()'
        self.assertTrue(compare_review_diff(pr(old), pr(new))['material'])

    def test_indentation_and_strings_are_material(self):
        for patch in ('-old()\n+    new()', '-old()\n+new("a  b")'):
            with self.subTest(patch=patch):
                self.assertTrue(compare_review_diff(pr('-old()\n+new()'), pr(patch))['material'])

    def test_missing_patch_requires_review(self):
        self.assertTrue(compare_review_diff(pr(None), pr(None))['material'])

    def test_missing_file_inventory_requires_review(self):
        self.assertTrue(compare_review_diff({}, {})['material'])

    def test_truncated_patch_requires_review(self):
        value = pr('-a\n+b', additions=20, deletions=1)
        self.assertTrue(compare_review_diff(value, value)['material'])

    def test_target_branch_change_requires_review(self):
        value = pr('-a\n+b')
        self.assertTrue(compare_review_diff({**value, 'base_ref': 'main'},
                                            {**value, 'base_ref': 'release'})['material'])

    def test_rename_requires_review(self):
        self.assertTrue(compare_review_diff(pr('-a\n+b'),
            pr('-a\n+b', 'renamed.py', status='renamed', previous_filename='app.py'))['material'])

    def test_mixed_dependency_and_code_update(self):
        old = pr('-old()\n+new()')
        new = pr('-old()\n+different()')
        new['files'] += pr('-a\n+b', 'uv.lock')['files']
        self.assertTrue(compare_review_diff(old, new)['material'])

    def test_explicit_request_overrides_equivalent_diff(self):
        value = pr('-a\n+b')
        self.assertTrue(compare_review_diff(value, value, force=True)['material'])

    def test_package_dependency_update_but_not_script_update(self):
        old = pr('-old\n+new', 'package.json',
                 content='{"dependencies":{"x":"1"},"scripts":{"test":"safe"}}')
        deps = pr('-old\n+newer', 'package.json',
                  content='{"dependencies":{"x":"2"},"scripts":{"test":"safe"}}')
        script = pr('-old\n+danger', 'package.json',
                    content='{"dependencies":{"x":"2"},"scripts":{"test":"other"}}')
        self.assertFalse(compare_review_diff(old, deps)['material'])
        self.assertTrue(compare_review_diff(old, script)['material'])

    def test_pyproject_dependency_update_but_not_build_config(self):
        old = pr('-a\n+b', 'pyproject.toml', content='[project]\nname="x"\ndependencies=["x==1"]')
        deps = pr('-a\n+c', 'pyproject.toml', content='[project]\nname="x"\ndependencies=["x==2"]')
        other = pr('-a\n+d', 'pyproject.toml', content='[project]\nname="other"\ndependencies=["x==2"]')
        self.assertFalse(compare_review_diff(old, deps)['material'])
        self.assertTrue(compare_review_diff(old, other)['material'])
