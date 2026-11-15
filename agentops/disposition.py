"""Public review determinations derived from qualified, saved review results."""


def determination(result, author, actor):
    findings = result.get('findings', [])
    severities=('critical','important','question') if result.get('require_decision') else ('critical','important')
    blocking = [f for f in findings if f.get('severity') in severities]
    qualified = (result.get('outcome') == 'ready' and
                 result.get('publication_qualification', 'ready') == 'ready' and
                 not result.get('qualification_limits') and not findings)
    if qualified and author.lower() != actor.lower():
        event, title = 'APPROVE', 'Approved after review and qualification.'
    elif blocking and author.lower() in ('dependabot[bot]', 'dependabot'):
        event, title = 'COMMENT', 'A maintainer fix is required; Dependabot is not asked to make changes.'
    elif blocking and author.lower() != actor.lower():
        event, title = 'REQUEST_CHANGES', 'Changes required before this PR can be approved.'
    else:
        event, title = 'COMMENT', 'Review completed; approval remains pending the items below.'
    lines = [title]
    if result.get('head_sha'):
        lines.append(f'Reviewed head `{result["head_sha"]}` against main `{result.get("main_sha", "unknown")}`.')
    if event=='APPROVE' and result.get('approval_source_baseline'):
        lines.append('Inherited workflow execution holds remain recorded separately; this code review does not authorize CI execution.')
    for finding in findings:
        location = finding.get('path', '')
        if finding.get('line', 0) > 0:
            location += ':' + str(finding['line'])
        lines.append(f'**{finding.get("severity", "finding").title()} — `{location}`**\n\n{finding["description"]}')
    limits = result.get('qualification_limits', [])
    if limits:
        lines.append('Qualification still required:\n' + '\n'.join('- ' + str(limit) for limit in limits))
    if not findings and not limits and not qualified:
        lines.append(result.get('summary') or 'The saved review does not establish full approval readiness.')
    return {'kind': 'review', 'event': event, 'body': '\n\n'.join(lines)}


def has_determination(current, result, actor):
    action = determination(result, current['author'], actor)
    expected = {'APPROVE': 'APPROVED', 'REQUEST_CHANGES': 'CHANGES_REQUESTED', 'COMMENT': 'COMMENTED'}[action['event']]
    for review in current.get('reviews', []):
        if ((review.get('user') or {}).get('login', '').lower() != actor.lower() or
                review.get('commit_id') != current['head_sha'] or review.get('state') != expected):
            continue
        if expected != 'COMMENTED' or review.get('body', '').startswith(action['body']):
            return True
    return False
