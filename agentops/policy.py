"""Pure revision and evidence gates. A green check is never sufficient alone."""

import re

from .planning import HOLDS
from .approval_baseline import inherited_approval_source
from .checks import effective_ci


def revision_gate(original, current, main_sha, live_main):
    reasons = []
    if current.get('state', 'open') != 'open':
        reasons.append('PR is no longer open')
    if main_sha != live_main:
        reasons.append('upstream main changed')
    for key in ('head_sha', 'discussion_hash', 'base_ref', 'base_sha', 'head_repo', 'head_ref'):
        if original.get(key) != current.get(key):
            reasons.append(f'{key} changed')
    return reasons


def terminal_ci_refresh(original, current, main_sha):
    if current.get('expected_workflows_verified') is not True:
        return None
    checks,workflows=effective_ci(current)
    paths = {w.get('path', '').split('@', 1)[0] for w in workflows
             if w.get('head_sha') == current['head_sha']}
    if not set(current.get('expected_workflows', [])) <= paths:
        return None
    for item in checks+workflows:
        allowed = ('success',) if item in workflows else ('success', 'neutral', 'skipped')
        if (item.get('head_sha') != current['head_sha'] or item.get('status') != 'completed' or
                item.get('conclusion') not in allowed):
            return None
    seen = set()
    for status in current.get('statuses', []):
        if status['context'] not in seen:
            if status['state'] != 'success':
                return None
            seen.add(status['context'])
    return {'head_sha': current['head_sha'], 'main_sha': main_sha,
            'from_hash': original.get('checks_hash'), 'to_hash': current.get('checks_hash'),
            'manifest_hash': current.get('workflow_manifest_hash'), 'terminal_success': True}


def gate(original, current, main_sha, live_main, result, notices):
    reasons = revision_gate(original, current, main_sha, live_main)
    if original.get('checks_hash') != current.get('checks_hash'):
        refresh = result.get('ci_refresh')
        verified_refresh = terminal_ci_refresh(original, current, live_main)
        if not verified_refresh or refresh != verified_refresh:
            reasons.append('Hosted check evidence changed')
    if current['number'] in HOLDS:
        reasons.append(HOLDS[current['number']])
    if current.get('mergeable') != 'MERGEABLE':
        reasons.append('Mergeability is not confirmed')
    coverage = result.get('coverage', {})
    if not {f['filename'] for f in current.get('files', [])} <= set(coverage.get('files', [])):
        reasons.append('Changed-file coverage is incomplete')
    if not {t['id'] for t in current.get('threads', [])} <= set(coverage.get('threads', [])):
        reasons.append('Review thread coverage is incomplete')
    for notice in notices:
        if notice['kind'] in ('security', 'policy'):
            reasons.append(notice.get('body', 'Active policy/security hold'))
    if any(f.get('severity') in ('critical', 'important', 'question') for f in result.get('findings', [])):
        reasons.append('Unresolved findings require follow-up')
    if not any(e.get('kind') == 'test' and e.get('status') == 'passed' for e in result.get('evidence', [])):
        reasons.append('Passing test evidence is missing')
    source_review = result.get('source_review', {})
    if not inherited_approval_source(current,live_main,result) and (not current.get('workflow_manifest_hash') or
            source_review.get('manifest_hash') != current.get('workflow_manifest_hash') or
            source_review.get('verdict') != 'clear' or not source_review.get('rationale') or
            not source_review.get('evidence')):
        reasons.append('Exact-source provenance review is incomplete')
    exceptions = set()
    for notice in notices:
        proof = notice.get('provenance', {})
        if (notice['kind'] == 'baseline' and proof.get('verified') is True and
                proof.get('head_sha') == current['head_sha'] and proof.get('main_sha') == live_main and
                proof.get('checks_hash') == current.get('checks_hash') and
                re.fullmatch('[a-f0-9]{64}', proof.get('artifact_sha256', ''))):
            exceptions.update(proof.get('check_ids', []))
    checks,workflows = effective_ci(current)
    if current.get('expected_workflows_verified') is not True:
        reasons.append('Expected workflow coverage has not been verified from exact-head source')
    observed_paths = {run.get('path', '').split('@', 1)[0] for run in workflows
                      if run.get('head_sha') == current['head_sha']}
    for missing in sorted(set(current.get('expected_workflows', [])) - observed_paths):
        reasons.append('Expected workflow has no actual exact-head run: ' + missing)
    if not workflows and current.get('expected_workflows'):
        reasons.append('No actual hosted workflows observed; expected coverage unresolved')
    for item in checks + workflows:
        allowed = ('success',) if item in workflows else ('success', 'neutral', 'skipped')
        if item.get('head_sha') != current['head_sha']:
            reasons.append(f'Check/run {item.get("id")} is not tied to the exact head')
        permission_wait=(item in workflows and (item.get('status') in ('action_required','queued','waiting','requested') or
                                               item.get('conclusion')=='action_required') and
                             inherited_approval_source(current,live_main,result))
        if item.get('status') != 'completed':
            if not permission_wait:
                reasons.append(f'Check/run {item.get("id")} is not terminal')
        elif not permission_wait and item.get('conclusion') not in allowed and item.get('id') not in exceptions:
            reasons.append(f'Check/run {item.get("id")} is not successful or baseline-qualified')
    # GitHub returns historical status updates; only the newest per context applies.
    contexts = {}
    for status in current.get('statuses', []):
        contexts.setdefault(status['context'], status)
    for context, status in contexts.items():
        if status['state'] != 'success' and status.get('id') not in exceptions:
            reasons.append(f'Status {context} is not successful or baseline-qualified')
    return list(dict.fromkeys(reasons))
