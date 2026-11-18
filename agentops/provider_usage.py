"""Read account allowances without inference; never return credentials or raw errors."""

import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import subprocess
import threading
import time
import tomllib
import urllib.request


PROVIDERS = ('codex', 'claude', 'copilot', 'devin')

# The status service validates CLI identity metadata; keep in step with the
# worker image's pinned Devin release.
DEVIN_CLI_VERSION = '3000.13.4'


def staged_fingerprint(provider, files, env):
    value = [provider, sorted((name, hashlib.sha256(content).hexdigest()) for name, content in files.items()),
             sorted((name, hashlib.sha256(str(content).encode()).hexdigest()) for name, content in env.items())]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def native_fingerprint(credential):
    return staged_fingerprint('copilot', {}, {'COPILOT_GITHUB_TOKEN': credential})


def authentication_fingerprint(config):
    if config['harness'] == 'copilot' and config.get('copilot_native'):
        if config.get('_native_credential_fingerprint'):
            return config['_native_credential_fingerprint']
        from .copilot_relay import native_credential
        return native_fingerprint(native_credential(config.get('copilot_cli_root'), config.get('copilot_account')))
    from .worker import auth_plan
    auth = auth_plan(config['harness'], os.environ, str(auth_root(config)), {'copilot', 'read:user'})
    return staged_fingerprint(config['harness'],
        {name: Path(source).read_bytes() for name, source in auth['files'].items()}, auth['env'])


def default_auth_root(provider):
    if provider == 'devin':
        if os.name == 'nt':
            return Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming') / 'devin'
        return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'devin'
    variable, folder = {'codex': ('CODEX_HOME', '.codex'), 'claude': ('CLAUDE_CONFIG_DIR', '.claude'),
                        'copilot': ('COPILOT_HOME', '.copilot')}[provider]
    return Path(os.environ.get(variable) or Path.home() / folder)


def auth_root(config):
    if config.get('auth_root'):
        return Path(config['auth_root'])
    return default_auth_root(config['harness'])


def subscription_policy_error(config):
    if config['harness'] != 'codex':
        return None
    api = bool(os.environ.get('OPENAI_API_KEY'))
    if api:
        return 'Codex reviews require subscription sign-in; API billing is disabled.'
    try:
        data = json.loads((auth_root(config) / 'auth.json').read_text(encoding='utf8'))
        api = isinstance(data, dict) and (bool(data.get('OPENAI_API_KEY')) or data.get('auth_mode') in ('apikey', 'api_key'))
    except (OSError, ValueError):
        pass
    return 'Codex reviews require subscription sign-in; API billing is disabled.' if api else None


def credential_scope(config):
    """Invalidate observations when the selected account or staged credentials change."""
    provider = config['harness']
    root = auth_root(config).expanduser().resolve()
    identity = [provider, str(root), str(config.get('copilot_account') or ''),
                str(config.get('copilot_cli_root') or ''), bool(config.get('copilot_native')),
                config.get('_native_credential_fingerprint')]
    filename = {'codex': 'auth.json', 'claude': '.credentials.json', 'devin': 'credentials.toml'}.get(provider)
    if filename:
        try:
            with (root / filename).open('rb') as stream:
                identity.append(hashlib.sha256(stream.read(1024 * 1024)).hexdigest())
        except OSError:
            identity.append('unavailable')
    for name in {'codex': ('CODEX_API_KEY', 'OPENAI_API_KEY'),
                 'claude': ('CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY'),
                 'copilot': ('AGENTOPS_COPILOT_TOKEN',), 'devin': ()}[provider]:
        identity.append(hashlib.sha256(os.environ.get(name, '').encode()).hexdigest())
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def number(value):
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid allowance quantity')
    return value


def timestamp(value):
    if type(value) in (int, float):
        return number(value)
    if not isinstance(value, str):
        raise ValueError('Missing allowance reset time')
    parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        # Provider date-only monthly resets use UTC.
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Ambiguous allowance reset time')
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return number(parsed.timestamp())


def window(identity, label, used, reset, minutes, period=None):
    minutes = number(minutes)
    return {'id': identity, 'label': label, 'used_percent': number(used),
            'resets_at': timestamp(reset), 'window_minutes': minutes,
            'period': period or ('weekly' if minutes == 10080 else 'short')}


def parse_codex(raw):
    buckets = raw.get('rateLimitsByLimitId') or {'codex': raw.get('rateLimits') or {}}
    windows = []
    blocked = raw.get('ordinaryUsageAllowed') is False
    for identity, bucket in buckets.items():
        if not isinstance(bucket, dict):
            raise ValueError('Invalid Codex allowance bucket')
        blocked = blocked or bool(bucket.get('rateLimitReachedType'))
        for slot in ('primary', 'secondary'):
            entry = bucket.get(slot)
            if entry is None:
                continue
            minutes = number(entry.get('windowDurationMins'))
            label = 'Weekly' if minutes == 10080 else f'{minutes:g} minute window'
            windows.append(window(f'{identity}:{slot}', f'{str(identity)[:80]} · {label}',
                                  entry.get('usedPercent'), entry.get('resetsAt'), minutes))
    if not windows:
        raise ValueError('Codex did not report allowance windows')
    return {'source': 'Codex account rate limits', 'blocked': blocked, 'windows': windows}


def parse_claude(raw):
    windows = []
    for key, entry in raw.items():
        if (key != 'five_hour' and not re.fullmatch(r'seven_day(?:_[a-z0-9_]+)?', key)) or entry is None:
            continue
        weekly = key.startswith('seven_day')
        label = 'Weekly' if key == 'seven_day' else '5 hour window' if not weekly else key.replace('_', ' ').title()
        windows.append(window(key, label, entry.get('utilization'), entry.get('resets_at'),
                              10080 if weekly else 300))
    if not windows:
        raise ValueError('Claude did not report allowance windows')
    return {'source': 'Claude account usage', 'blocked': False, 'windows': windows}


def quantity(value):
    """Connect-JSON encodes 64-bit integers as strings; coerce before validation."""
    if isinstance(value, str) and re.fullmatch(r'-?\d+', value):
        return int(value)
    return value


def apply_token_budget(config, provider, result, reset=None):
    """Add a measured weekly consumption window when a token budget is set.

    Consumption is summed by the caller from this coordinator's own execution
    records; the window ends at the provider's weekly reset so local spend is
    bounded by the same period as account quota.
    """
    budget = (config.get('provider_limits') or {}).get(provider, {}).get('token_budget')
    consumed_since = config.get('_consumed_since')
    if reset is None:
        reset = next((w['resets_at'] for w in result.get('windows', [])
                      if w['period'] == 'weekly'), None)
    if not (budget and consumed_since and reset):
        return result
    budget = number(budget)
    consumed = consumed_since(number(reset) - 604800)
    result['windows'].append(window(f'{provider}-tokens', 'Weekly token budget (measured)',
                                    consumed / budget * 100, reset, 10080, 'weekly'))
    result['blocked'] = result.get('blocked') or consumed >= budget
    return result


def parse_devin(raw):
    status = (raw.get('userStatus') or {}).get('planStatus') or {}
    plan = status.get('planInfo') or {}
    windows, blocked = [], False
    reported = {'weekly': ('weeklyQuotaRemainingPercent', 'weeklyQuotaResetAtUnix', 'Weekly quota', 10080),
                'daily': ('dailyQuotaRemainingPercent', 'dailyQuotaResetAtUnix', 'Daily quota', 1440)}
    for identity, (remaining_key, reset_key, label, minutes) in reported.items():
        remaining, reset = quantity(status.get(remaining_key)), quantity(status.get(reset_key))
        if remaining is None or reset is None:
            continue
        blocked = blocked or number(remaining) <= 0
        windows.append(window(identity, label, max(0.0, 100 - number(remaining)), reset, minutes))
    for identity, consumed_key, limit_key, label in (
            ('acu', 'acuConsumed', 'acuLimit', 'Monthly ACU consumption'),
            ('dollars', 'dollarsConsumed', 'dollarsLimit', 'Monthly spend limit')):
        limit = quantity(status.get(limit_key))
        if not limit or number(limit) <= 0:
            continue
        consumed = number(quantity(status.get(consumed_key)) or 0)
        blocked = blocked or consumed >= number(limit)
        windows.append(window(identity, label, consumed / number(limit) * 100,
                              status.get('planEnd'), 43200, 'monthly'))
    available, monthly = quantity(status.get('availablePromptCredits')), quantity(plan.get('monthlyPromptCredits'))
    if (type(monthly) is int and monthly > 0 and type(available) in (int, float)):
        blocked = blocked or available <= 0
        windows.append(window('prompt_credits', 'Monthly prompt credits',
                              max(0.0, monthly - available) / monthly * 100,
                              status.get('planEnd'), 43200, 'monthly'))
    quota_billed = plan.get('billingStrategy') == 'BILLING_STRATEGY_QUOTA'
    unlimited = not windows and (available == -1 or monthly == -1) and not quota_billed
    if not windows and not unlimited:
        raise ValueError('Devin did not report allowance windows')
    return {'source': 'Devin account plan status', 'blocked': blocked,
            'windows': windows, 'unlimited': unlimited}


def devin_status(base_url, api_key):
    """Bounded Connect-JSON read of the same plan status the CLI displays."""
    body = json.dumps({'metadata': {'api_key': api_key, 'ide_name': 'devin',
                                    'ide_version': DEVIN_CLI_VERSION,
                                    'extension_name': 'devin',
                                    'extension_version': DEVIN_CLI_VERSION}}).encode()
    request = urllib.request.Request(
        base_url.rstrip('/') + '/exa.seat_management_pb.SeatManagementService/GetUserStatus',
        data=body, method='POST', headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=15) as response:
        content = response.read(4 * 1024 * 1024 + 1)
    if len(content) > 4 * 1024 * 1024:
        raise ValueError('Allowance response too large')
    return json.loads(content)


def parse_copilot(raw):
    windows = []
    snapshots = raw.get('quota_snapshots') or raw.get('quotaSnapshots') or {}
    unlimited = False
    blocked = False
    for key, entry in snapshots.items():
        # Completion entitlements do not apply to the reviewing chat harness.
        if key == 'completions':
            continue
        entitlement = entry.get('entitlement', entry.get('entitlementRequests'))
        if entry.get('unlimited') is True or entitlement == -1:
            unlimited = True
            continue
        blocked = blocked or entry.get('has_quota') is False
        remaining = entry.get('percent_remaining', entry.get('remainingPercentage'))
        if remaining is None:
            entitlement = number(entitlement)
            if not entitlement:
                raise ValueError('Unknown Copilot allowance')
            used = entry.get('usedRequests')
            if used is None:
                used = entitlement - number(entry.get('remaining'))
            percent = number(used) / entitlement * 100
        else:
            percent = 100 - number(remaining)
        reset = (entry.get('quota_reset_at') or entry.get('resetDate') or entry.get('reset_date')
                 or raw.get('quota_reset_date_utc') or raw.get('quota_reset_date'))
        minutes = entry.get('windowDurationMins', entry.get('window_duration_mins', 0))
        weekly = minutes == 10080 or key in ('weekly', 'weekly_usage', 'weekly_tokens')
        windows.append(window(key, key.replace('_', ' ').title(), percent, reset,
                              10080 if weekly else minutes, 'weekly' if weekly else 'monthly'))
    if not windows and not unlimited:
        raise ValueError('Copilot did not report allowance windows')
    return {'source': 'Copilot account entitlements', 'blocked': blocked,
            'windows': windows, 'unlimited': unlimited and not windows}


def codex_command():
    native = shutil.which('codex.exe') if os.name == 'nt' else shutil.which('codex')
    if native:
        return [native, 'app-server', '--listen', 'stdio://']
    # npm's Windows shim may wrap PowerShell, which buffers stdin. Resolve the
    # vendor binary rather than going through a shell or changing user wrappers.
    node = shutil.which('node')
    shim = shutil.which('codex')
    roots = [Path(p).resolve().parent for p in (node, shim) if p]
    arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
    triple = 'aarch64' if arch == 'arm64' else 'x86_64'
    for root in roots:
        package = root / 'node_modules/@openai/codex'
        binary = package / f'node_modules/@openai/codex-win32-{arch}/vendor/{triple}-pc-windows-msvc/bin/codex.exe'
        if binary.is_file():
            return [str(binary), 'app-server', '--listen', 'stdio://']
        if node and (package / 'bin/codex.js').is_file():
            return [node, str(package / 'bin/codex.js'), 'app-server', '--listen', 'stdio://']
    raise ValueError('Install the Codex CLI to read subscription allowance')


def codex_rpc(command, env, timeout=20):
    """One bounded, read-only stdio connection; no threads or turns are created."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding='utf8', env=env or None,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    replies = queue.Queue()
    deadline = time.monotonic() + timeout

    def read():
        try:
            while True:
                line = process.stdout.readline(1024 * 1024)
                if not line or len(line) >= 1024 * 1024:
                    break
                message = json.loads(line)
                if message.get('id') in (1, 2):
                    replies.put(message)
        except (OSError, ValueError):
            pass
        finally:
            replies.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    def send(message):
        process.stdin.write(json.dumps(message) + '\n')
        process.stdin.flush()

    def receive(identity):
        try:
            result = replies.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise TimeoutError('Codex allowance request timed out') from None
        if result is None or result.get('id') != identity or 'result' not in result:
            raise ValueError('Codex allowance is unavailable; check subscription sign-in')
        return result['result']

    try:
        send({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'agentops_allowance', 'version': '1.0'}}})
        receive(1)
        send({'method': 'initialized'})
        send({'id': 2, 'method': 'account/rateLimits/read'})
        return receive(2)
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        reader.join(timeout=1)
        process.stdout.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward account credentials to a redirected host.
        return None


def account_json(url, headers):
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=15) as response:
        content = response.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise ValueError('Allowance response too large')
    return json.loads(content)


def read_usage(config):
    provider = config['harness']
    if provider == 'codex':
        if subscription_policy_error(config):
            raise ValueError('Codex reviews require subscription authentication')
        content = (auth_root(config) / 'auth.json').read_bytes()
        account = (json.loads(content).get('tokens') or {}).get('account_id')
        if not account:
            raise ValueError('Codex subscription account identity is unavailable')
        env = dict(os.environ)
        env['CODEX_HOME'] = str(auth_root(config))
        env.pop('OPENAI_API_KEY', None)
        env.pop('CODEX_API_KEY', None)
        raw = codex_rpc(codex_command(), env)
        # The CLI reads credentials itself. Bind its actual response account to
        # the file snapshot, including an A->B->A switch during the RPC.
        if raw.get('accountId') != account:
            raise ValueError('Codex allowance account does not match worker authentication')
        result = apply_token_budget(config, provider, parse_codex(raw))
        fingerprint = staged_fingerprint(provider, {'auth.json': content}, {})
    elif provider == 'claude':
        if os.environ.get('ANTHROPIC_API_KEY'):
            raise ValueError('Subscription allowance cannot authorize API-key authentication')
        content = (auth_root(config) / '.credentials.json').read_bytes()
        credential = json.loads(content).get('claudeAiOauth', {}).get('accessToken')
        if not credential:
            raise ValueError('Claude subscription sign-in required')
        result = apply_token_budget(config, provider, parse_claude(
            account_json('https://api.anthropic.com/api/oauth/usage', {
                'Authorization': 'Bearer ' + credential, 'anthropic-beta': 'oauth-2025-04-20'})))
        fingerprint = staged_fingerprint(provider, {'.credentials.json': content}, {})
    elif provider == 'copilot':
        if config.get('copilot_native'):
            from .copilot_relay import native_credential
            credential = native_credential(config.get('copilot_cli_root'), config.get('copilot_account'))
            fingerprint = native_fingerprint(credential)
            expected = config.get('_native_credential_fingerprint')
            if expected and fingerprint != expected:
                raise ValueError('Native account changed; existing relay allowance cannot be verified')
        else:
            if os.environ.get('COPILOT_PROVIDER_BASE_URL'):
                raise ValueError('Copilot entitlement cannot authorize an external BYOK provider')
            credential = os.environ.get('AGENTOPS_COPILOT_TOKEN')
            if not credential:
                raise ValueError('Copilot sign-in required')
            fingerprint = staged_fingerprint(provider, {}, {'COPILOT_GITHUB_TOKEN': credential})
        result = apply_token_budget(config, provider, parse_copilot(
            account_json('https://api.github.com/copilot_internal/user', {
                'Authorization': 'token ' + credential, 'Accept': 'application/json',
                'User-Agent': 'HeadroomAgentOps'})))
    elif provider == 'devin':
        try:
            content = (auth_root(config) / 'credentials.toml').read_bytes()
            stored = tomllib.loads(content.decode('utf8'))
        except (OSError, ValueError):
            raise ValueError('Devin sign-in required') from None
        credential = stored.get('windsurf_api_key')
        if not isinstance(credential, str) or not credential:
            raise ValueError('Devin sign-in required')
        raw = devin_status(stored.get('api_server_url') or 'https://server.codeium.com', credential)
        try:
            result = parse_devin(raw)
        except ValueError:
            result = {'source': 'Devin account plan status', 'blocked': False,
                      'windows': [], 'unlimited': False}
        plan = (raw.get('userStatus') or {}).get('planStatus') or {}
        result = apply_token_budget(config, provider, result,
                                    quantity(plan.get('weeklyQuotaResetAtUnix')))
        if not result['windows'] and not result.get('unlimited'):
            raise ValueError('Devin did not report allowance windows')
        fingerprint = staged_fingerprint(provider, {'credentials.toml': content}, {})
    else:
        raise ValueError('Unknown allowance provider')
    return {**result, 'auth_fingerprint': fingerprint}
