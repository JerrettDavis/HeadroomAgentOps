"""Bind review-only inheritance evidence without granting CI execution permission."""
import re
from .model import digest


def execution_sensitive_delta(current):
    pattern=r'^(\.github|scripts|docker|e2e)/|(^|/)(Dockerfile[^/]*|Makefile)$'
    return any(re.search(pattern,f['filename'],re.I) for f in current.get('files',[]))


def attach_approval_baseline(github,evidence,current,main_sha,result):
    if (current.get('base_ref','main')!='main' or execution_sensitive_delta(current) or
            not current.get('workflow_manifest_hash') or not hasattr(github,'review_workflow_baseline')):
        return
    baseline=github.review_workflow_baseline(current,main_sha)
    if baseline['manifest_hash']!=current['workflow_manifest_hash'] or baseline['review_events']:
        return
    proof={'scope':'review_only_no_execution','head_sha':current['head_sha'],'main_sha':main_sha,
           'manifest_hash':current['workflow_manifest_hash'],'merge_base_sha':baseline.get('merge_base_sha'),
           'base_ref':current.get('base_ref','main'),
           'paths_hash':digest(sorted(f['filename'] for f in current.get('files',[])))}
    result['approval_source_baseline']={**proof,'evidence':evidence.put(proof)}


def inherited_approval_source(current,main_sha,result):
    proof=result.get('approval_source_baseline',{})
    return (proof.get('scope')=='review_only_no_execution' and
            proof.get('head_sha')==current['head_sha'] and proof.get('main_sha')==main_sha and
            proof.get('base_ref')==current.get('base_ref','main')=='main' and
            proof.get('manifest_hash')==current.get('workflow_manifest_hash') and
            proof.get('paths_hash')==digest(sorted(f['filename'] for f in current.get('files',[]))) and
            bool(re.fullmatch('[a-f0-9]{64}',proof.get('evidence',{}).get('sha256',''))) and
            not execution_sensitive_delta(current))
