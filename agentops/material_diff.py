"""Conservative, deterministic comparison of PR contributions for scheduling.

This classification never grants approval or permission to execute code.
"""
import io
import json
import difflib
from pathlib import PurePosixPath
import re
import tokenize
import tomllib


DEPENDENCY_FILES = frozenset({
    'uv.lock', 'poetry.lock', 'Pipfile.lock', 'package-lock.json',
    'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'Cargo.lock',
    'go.sum', 'Gemfile.lock', 'composer.lock',
})


def dependency_only(old, new):
    if old.get('status') != 'modified' or new.get('status') != 'modified':
        return False
    path = PurePosixPath(new['filename']).name
    if path in DEPENDENCY_FILES:
        return True
    if path not in ('package.json', 'pyproject.toml'):
        return False
    if not all(isinstance(f.get('content'), str) for f in (old, new)):
        return False
    try:
        load = json.loads if path == 'package.json' else tomllib.loads
        before, after = load(old['content']), load(new['content'])
        if path == 'package.json':
            for value in (before, after):
                for key in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'):
                    value.pop(key, None)
        else:
            for value in (before, after):
                project = value.get('project', {})
                project.pop('dependencies', None)
                project.pop('optional-dependencies', None)
                value.pop('dependency-groups', None)
        return before == after
    except (ValueError, TypeError, AttributeError):
        return False


def _tokens(file):
    content = file.get('content')
    if not file['filename'].endswith('.py') or not isinstance(content, str):
        return None
    try:
        values = list(tokenize.generate_tokens(io.StringIO(content).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    if any(t.type == tokenize.ERRORTOKEN for t in values):
        return None
    return [(t.type, '\n' if t.type == tokenize.NEWLINE else t.string) for t in values
            if t.type not in (tokenize.NL, tokenize.ENDMARKER)]


def _contribution(file):
    patch = file.get('patch')
    if not isinstance(patch, str) or not patch:
        return None
    lines = []
    for line in patch.split('\n'):
        hunk = re.match(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(.*)$', line)
        if hunk:
            # Positions change when main grows; context still identifies the
            # edited region so moving an edit to another function is material.
            lines.append('@@' + hunk[1])
            continue
        if line.startswith(('+', '-', ' ')):
            lines.append(line)
    if any(file.get(key) is not None and file[key] != sum(line.startswith(prefix) for line in lines)
           for key, prefix in (('additions', '+'), ('deletions', '-'))):
        return None
    return (file.get('status'), file.get('previous_filename'), tuple(lines))


def hunk_coordinates(file):
    return [(int(start), int(count or 1)) for start, count in
            re.findall(r'^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@',
                       file.get('patch') or '', re.M)]


def _positions_correspond(old, new):
    before, after = hunk_coordinates(old), hunk_coordinates(new)
    if before == after:
        return True
    if (len(before) != len(after) or not isinstance(old.get('base_content'), str) or
            not isinstance(new.get('base_content'), str)):
        return False
    blocks = difflib.SequenceMatcher(None, old['base_content'].splitlines(),
                                    new['base_content'].splitlines(), autojunk=False).get_matching_blocks()
    # Every complete old hunk must map into the same unchanged base region.
    # Repeated contexts alone cannot prove that an edit stayed in place.
    return all(count == new_count and count > 0 and any(
        block.a <= start - 1 and start - 1 + count <= block.a + block.size and
        block.b + start - 1 - block.a == new_start - 1 for block in blocks)
        for (start, count), (new_start, new_count) in zip(before, after))


def compare_review_diff(previous, current, force=False):
    """Return an auditable scheduling decision; incomplete evidence fails open."""
    if force:
        return {'material': True, 'reason': 'explicit_re_review', 'changed_files': []}
    if previous.get('base_ref') != current.get('base_ref'):
        return {'material': True, 'reason': 'target_branch_changed', 'changed_files': []}
    if not isinstance(previous.get('files'), list) or not isinstance(current.get('files'), list):
        return {'material': True, 'reason': 'incomplete_diff', 'changed_files': []}
    before = {f['filename']: f for f in previous['files']}
    after = {f['filename']: f for f in current['files']}
    changed = []
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if old is None or new is None:
            changed.append(path)
            continue
        left, right = _contribution(old), _contribution(new)
        if left is None or right is None:
            return {'material': True, 'reason': 'incomplete_diff', 'changed_files': [path]}
        if left == right and not _positions_correspond(old, new):
            changed.append(path)
            continue
        if left != right:
            old_tokens, new_tokens = _tokens(old), _tokens(new)
            if (old_tokens is not None and old_tokens == new_tokens and
                    left[:2] == right[:2]):
                continue
            changed.append(path)
    if not changed:
        return {'material': False, 'reason': 'equivalent_contribution', 'changed_files': []}
    dependencies = all(dependency_only(before.get(path, {}), after.get(path, {}))
                       for path in changed)
    return {'material': not dependencies,
            'reason': 'dependency_only' if dependencies else 'material_diff',
            'changed_files': changed}
