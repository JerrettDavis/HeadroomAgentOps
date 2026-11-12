"""Small CLI adapters. All commands execute only inside isolated containers."""

import json
import uuid


def usage_from_events(harness, events):
    if harness == 'codex':
        turns = [e['usage'] for e in events if e.get('type') == 'turn.completed' and e.get('usage')]
        if not turns:
            return None
        return {key: sum(turn.get(key, 0) for turn in turns) for key in
                ('input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_output_tokens')}
    if harness == 'claude':
        final = next((e for e in reversed(events) if e.get('type') == 'result' and e.get('usage')), None)
        if final:
            return {**final['usage'], 'cost_usd': final.get('total_cost_usd'), 'models': final.get('modelUsage')}
    return None


def response_from_events(harness, events):
    if harness == 'copilot':
        final = next((e for e in reversed(events) if e.get('type') == 'assistant.message'), None)
        if not final or not isinstance(final.get('data', {}).get('content'), str):
            raise ValueError('Copilot did not produce a final assistant response')
        text = final['data']['content'].strip()
        if text.startswith('```json\n') and text.endswith('\n```'):
            text = text[8:-4]
        return json.loads(text)
    final = next((e for e in reversed(events) if e.get('type') == 'result'), None)
    if not final:
        raise ValueError('Harness did not produce a final structured result')
    if isinstance(final.get('structured_output'), dict):
        return final['structured_output']
    return json.loads(final['result'])


RESULT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['schema', 'session_id', 'number', 'head_sha', 'main_sha', 'outcome',
                 'summary', 'findings', 'evidence', 'actions', 'notices', 'coverage'],
    'properties': {
        'schema': {'type': 'integer', 'enum': [1]},
        'session_id': {'type': 'string'}, 'number': {'type': 'integer'},
        'head_sha': {'type': 'string'}, 'main_sha': {'type': 'string'},
        'outcome': {'type': 'string', 'enum': ['held', 'ready', 'reviewed', 'needs_followup', 'awaiting_ci']},
        'summary': {'type': 'string'},
        'source_review': {'type': 'object', 'additionalProperties': False,
            'required': ['manifest_hash', 'verdict', 'rationale', 'evidence'],
            'properties': {'manifest_hash': {'type': 'string'},
                           'verdict': {'type': 'string', 'enum': ['clear', 'held', 'unknown']},
                           'rationale': {'type': 'string'},
                           'evidence': {'type': 'array', 'items': {'type': 'string'}}}},
        'findings': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['severity', 'path', 'line', 'description', 'evidence'],
            'properties': {'severity': {'type': 'string', 'enum': ['critical', 'important', 'minor', 'question']},
                           'path': {'type': 'string'}, 'line': {'type': 'integer'},
                           'novel': {'type': 'boolean'},
                           'description': {'type': 'string'}, 'evidence': {'type': 'string'}}}},
        'evidence': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['kind', 'description', 'command', 'status'],
            'properties': {'kind': {'type': 'string', 'enum': ['static', 'test', 'live', 'reused_artifact', 'baseline']},
                           'description': {'type': 'string'}, 'command': {'type': 'string'},
                           'status': {'type': 'string', 'enum': ['passed', 'failed', 'not_run']}}}},
        'actions': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['kind', 'body', 'event', 'candidate_sha', 'workflow_id'],
            'properties': {'kind': {'type': 'string', 'enum': ['comment', 'review', 'push', 'ci']},
                           'body': {'type': 'string'}, 'event': {'type': 'string'},
                           'candidate_sha': {'type': 'string'}, 'workflow_id': {'type': 'integer'}}}},
        'notices': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['kind', 'body', 'evidence'], 'properties': {
                'kind': {'type': 'string', 'enum': ['baseline', 'security', 'learning', 'suggestion']},
                'body': {'type': 'string'}, 'evidence': {'type': 'string'},
                'scope': {'type': 'string', 'enum': ['pr', 'repository']}}}},
        'coverage': {'type': 'object', 'additionalProperties': False,
            'required': ['files', 'threads'], 'properties': {
                'files': {'type': 'array', 'description': 'Exact filename strings from PR files, without prose.',
                          'items': {'type': 'string'}},
                'threads': {'type': 'array', 'description': 'Exact review thread IDs, without prose or comment IDs.',
                            'items': {'type': 'string'}},
                'notices': {'type': 'array', 'items': {'type': 'string'}}}},
    },
}


def _validate(value, schema, path='result'):
    kind = schema.get('type')
    expected = {'object': dict, 'array': list, 'string': str, 'integer': int, 'boolean': bool}
    if kind and type(value) is not expected[kind]:
        raise ValueError(f'{path} must be {kind}')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path} has unsupported value')
    if kind == 'object':
        for required in schema.get('required', []):
            if required not in value:
                raise ValueError(f'{path}.{required} missing')
        for key, child in value.items():
            if key not in schema.get('properties', {}):
                if schema.get('additionalProperties') is False:
                    raise ValueError(f'{path}.{key} unknown')
            else:
                _validate(child, schema['properties'][key], f'{path}.{key}')
    elif kind == 'array':
        for index, child in enumerate(value):
            _validate(child, schema['items'], f'{path}[{index}]')


def validate_result(value, job):
    _validate(value, RESULT_SCHEMA)
    for key in ('session_id', 'number', 'head_sha', 'main_sha'):
        if value[key] != job[key]:
            raise ValueError(f'Result {key} does not match assigned job')
    if not value['summary'].strip():
        raise ValueError('Result requires a summary')
    if value['outcome'] == 'ready' and not any(
            item['kind'] == 'test' and item['status'] == 'passed' for item in value['evidence']):
        raise ValueError('Readiness requires passing test evidence and central qualification')
    return value


class Harness:
    def __init__(self, name, model=None):
        if name not in ('codex', 'claude', 'copilot'):
            raise ValueError(f'Unsupported harness {name}')
        self.name = name
        self.model = model

    def command(self, job):
        uuid.UUID(job['session_id'])
        prompt = job['prompt']
        if self.name == 'codex':
            command = ['codex', '--disable', 'multi_agent', '--ask-for-approval', 'never', 'exec', '--sandbox', 'danger-full-access',
                       '--ephemeral', '--json', '--output-schema', '/job/output/schema.json',
                       '--output-last-message', '/job/output/final.json']
        elif self.name == 'claude':
            command = ['claude', '--print', '--dangerously-skip-permissions', '--no-session-persistence',
                       '--session-id', job['session_id'], '--strict-mcp-config', '--mcp-config',
                       '{"mcpServers":{}}', '--setting-sources', '', '--output-format', 'stream-json', '--verbose',
                       '--json-schema', json.dumps(RESULT_SCHEMA),
                       '--tools', 'Bash,Read,Edit,Write,Glob,Grep', '--disable-slash-commands']
        else:
            command = ['copilot', '--autopilot', '--no-ask-user', '--allow-all',
                       '--max-autopilot-continues', '2', '--session-id', job['session_id'],
                       '--disable-builtin-mcps', '--no-custom-instructions', '--no-auto-update',
                       '--excluded-tools', 'task', 'read_agent', 'write_agent', 'list_agents',
                       '--sync-session=off', '--output-format', 'json', '--stream', 'off',
                       '--usage-output-file', '/job/output/usage.json']
        if self.model:
            command += ['--model', self.model]
        return command + (['--prompt', prompt] if self.name == 'copilot' else [prompt])
