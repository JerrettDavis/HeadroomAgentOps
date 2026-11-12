"""Conservative workflow applicability and execution-source screening."""

import re

import yaml


def _load(source):
    if len(source.encode('utf8')) > 524288:
        raise ValueError('Workflow exceeds source review budget')
    data = yaml.safe_load(source)
    if not isinstance(data, dict):
        raise ValueError('Workflow is not a mapping')
    # PyYAML follows YAML 1.1 and treats GitHub's top-level `on` key as boolean true.
    if True in data and 'on' not in data:
        data['on'] = data.pop(True)
    return data


def _match(pattern, value):
    if not isinstance(pattern, str) or any(c in pattern for c in '[]{}+?$'):
        return None
    expression, position = '', 0
    while position < len(pattern):
        if pattern[position:position + 3] == '**/':
            expression += '(?:.*/)?'
            position += 3
        elif pattern[position:position + 2] == '**':
            expression += '.*'
            position += 2
        elif pattern[position] == '*':
            expression += '[^/]*'
            position += 1
        else:
            expression += re.escape(pattern[position])
            position += 1
    return bool(re.fullmatch(expression, value))


def _patterns(patterns, value):
    if not isinstance(patterns, list):
        return None
    matched = False
    for pattern in patterns:
        negative = isinstance(pattern, str) and pattern.startswith('!')
        result = _match(pattern[1:] if negative else pattern, value)
        if result is None:
            return None
        if result:
            matched = not negative
    return matched


def _event_matches(filters, branch, paths):
    if filters in (None, ''):
        return True
    if not isinstance(filters, dict):
        return None
    if filters.get('types') and not set(filters['types']) & {'opened', 'reopened', 'synchronize'}:
        return False
    for key in ('branches', 'branches-ignore'):
        if key in filters:
            matched = _patterns(filters[key], branch)
            if matched is None:
                return None
            if (key == 'branches' and not matched) or (key == 'branches-ignore' and matched):
                return False
    if 'paths' in filters or 'paths-ignore' in filters:
        if len(paths) > 300:
            return None  # GitHub's changed-file trigger limit needs explicit qualification.
        key = 'paths' if 'paths' in filters else 'paths-ignore'
        matches = [_patterns(filters[key], path) for path in paths]
        if None in matches:
            return None
        return any(matches) if key == 'paths' else not all(matches)
    return True


def expected_workflows(sources, pr, upstream):
    expected, unknown = [], []
    paths = [f['filename'] for f in pr['files']]
    for path, source in sources.items():
        try:
            triggers = _load(source).get('on', {})
            if isinstance(triggers, str):
                triggers = {triggers: ''}
            elif isinstance(triggers, list):
                triggers = dict.fromkeys(triggers, '')
            if not isinstance(triggers, dict):
                raise ValueError('Unknown event declaration')
            matches = []
            if 'pull_request' in triggers:
                matches.append(_event_matches(triggers['pull_request'], pr['base_ref'], paths))
            if 'push' in triggers and pr['head_repo'] == upstream:
                matches.append(_event_matches(triggers['push'], pr['head_ref'], paths))
            if True in matches:
                expected.append(path)
            elif None in matches:
                unknown.append(path)
        except (ValueError, TypeError, yaml.YAMLError):
            unknown.append(path)
    return expected, unknown


def screen_source(source):
    reasons, visited = [], set()
    try:
        data = _load(source)
    except (ValueError, yaml.YAMLError):
        return ['Unparseable execution source']
    def walk(value, depth=0):
        if depth > 30 or len(visited) > 10000:
            reasons.append('Execution source graph exceeds review budget')
            return
        if isinstance(value, (dict, list)):
            if id(value) in visited:
                return
            visited.add(id(value))
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'uses' and isinstance(child, str):
                    if child.startswith('./'):
                        reasons.append('Local action dependency needs provenance review: ' + child)
                    elif child.startswith('docker://'):
                        if not re.search(r'@sha256:[a-f0-9]{64}$', child):
                            reasons.append('Mutable Docker action reference: ' + child)
                    elif not re.search(r'@[a-f0-9]{40}$', child):
                        reasons.append('Mutable action reference: ' + child)
                elif key in ('image', 'container') and isinstance(child, str):
                    if not re.search(r'@sha256:[a-f0-9]{64}$', child) and not child.startswith('Dockerfile'):
                        reasons.append('Container image lacks immutable digest: ' + child)
                elif key == 'run' and isinstance(child, str):
                    if re.search(r'\b(curl|wget|Invoke-WebRequest|iwr)\b.*https?://', child, re.S | re.I):
                        reasons.append('Downloaded execution inputs require recorded integrity proof')
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)
    walk(data)
    return list(dict.fromkeys(reasons))
