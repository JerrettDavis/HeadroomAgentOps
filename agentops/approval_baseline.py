"""Bind review-only inheritance evidence without granting CI execution permission."""
import re
from urllib.parse import quote
from .model import digest


def execution_sensitive_delta(current):
    pattern=r'^(\.github|scripts|docker|e2e)/|(^|/)(Dockerfile[^/]*|Makefile)$'
    return any(re.search(pattern,f['filename'],re.I) for f in current.get('files',[]))


def reviewed_source_delta(current, result):
    """Bind code-review evidence without clearing the separate execution verdict."""
    source = result.get('source_review', {})
    return (source.get('manifest_hash') == current.get('workflow_manifest_hash') and
            source.get('review_only_verdict') == 'clear' and
            (source.get('verdict') == 'clear' or
             (source.get('verdict') == 'held' and source.get('execution_hold_scope') == 'inherited_only'
              and isinstance(source.get('inherited_execution_paths'), list)
              and bool(source['inherited_execution_paths'])
              and all(isinstance(p, str) and p for p in source['inherited_execution_paths']))) and
            bool(source.get('rationale', '').strip()) and bool(source.get('evidence')) and
            not result.get('findings') and
            {f['filename'] for f in current.get('files', [])} <=
                set(result.get('coverage', {}).get('files', [])) and
            any(e.get('kind') == 'test' and e.get('status') == 'passed'
                for e in result.get('evidence', [])))


def attach_approval_baseline(github,evidence,current,main_sha,result):
    if (current.get('base_ref','main')!='main' or
            not current.get('workflow_manifest_hash') or not hasattr(github,'review_workflow_baseline')):
        return
    baseline=github.review_workflow_baseline(current,main_sha)
    changed = (execution_sensitive_delta(current) or
               baseline['manifest_hash'] != current['workflow_manifest_hash'])
    if baseline['review_events'] or (changed and not reviewed_source_delta(current, result)):
        return
    inherited = []
    if changed and result['source_review'].get('verdict') == 'held':
        paths = result['source_review']['inherited_execution_paths']
        changed_paths = {f['filename'] for f in current.get('files', [])}
        for path in sorted(set(paths)):
            if path in changed_paths:
                return
            blobs = [github.request('GET', f'repos/{github.repo}/contents/{quote(path, safe="/")}?ref={sha}')
                     for sha in (current['head_sha'], main_sha)]
            if (any(b.get('type') != 'file' for b in blobs) or
                    not re.fullmatch('[a-f0-9]{40}', blobs[0].get('sha', '')) or
                    blobs[0]['sha'] != blobs[1].get('sha')):
                return
            inherited.append({'path': path, 'sha': blobs[0]['sha']})
    proof={'scope':'review_only_no_execution','head_sha':current['head_sha'],'main_sha':main_sha,
           'manifest_hash':current['workflow_manifest_hash'],'merge_base_sha':baseline.get('merge_base_sha'),
           'base_ref':current.get('base_ref','main'),
           'paths_hash':digest(sorted(f['filename'] for f in current.get('files',[])))}
    if changed:
        proof['reviewed_source_delta'] = digest(result['source_review'])
        proof['inherited_execution_sources'] = inherited
    result['approval_source_baseline']={**proof,'evidence':evidence.put(proof)}


def inherited_approval_source(current,main_sha,result):
    proof=result.get('approval_source_baseline',{})
    return (proof.get('scope')=='review_only_no_execution' and
            proof.get('head_sha')==current['head_sha'] and proof.get('main_sha')==main_sha and
            proof.get('base_ref')==current.get('base_ref','main')=='main' and
            proof.get('manifest_hash')==current.get('workflow_manifest_hash') and
            proof.get('paths_hash')==digest(sorted(f['filename'] for f in current.get('files',[]))) and
            bool(re.fullmatch('[a-f0-9]{64}',proof.get('evidence',{}).get('sha256',''))) and
            ((proof.get('reviewed_source_delta') == digest(result.get('source_review', {})) and
              reviewed_source_delta(current, result) and
              (result['source_review'].get('verdict') == 'clear' or
               {p['path'] for p in proof.get('inherited_execution_sources', [])} ==
               set(result['source_review'].get('inherited_execution_paths', [])))) if proof.get('reviewed_source_delta')
             else not execution_sensitive_delta(current)))
