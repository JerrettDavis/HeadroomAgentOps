# PR auto-stacker

Status: approved 2026-11-24. Use one combined PR, with final merging by a human
on GitHub. User requires algorithmic, efficient preparation to minimize agent
tokens and judgement. Normal preparation makes zero model calls.

## Intent

Prepare a suite of compatible PRs together so a human can merge the suite
without manually preparing every PR. Agents prepare and diagnose the stack;
humans explicitly initiate publication and merging.

The existing merge-priority view ranks PRs and resolves declared dependencies.
It does not prove that independently mergeable PRs combine cleanly. This feature
must perform that integration check rather than treating ranking as validation.

## Proposed workflow

1. A human selects PRs from the current inventory and requests preparation.
   Include prerequisites or explain which missing prerequisites block preparation.
2. Save a durable stack record with repository, target branch and SHA, ordered
   source PRs and head SHAs, initiator, timestamps, and preparation results.
3. In an isolated checkout, combine the pinned source heads in dependency order.
   Verify the entire cumulative combination, including overlapping edits; file
   overlap alone neither proves a conflict nor establishes compatibility.
4. Report conflicts by PR and path. Agents may explain conflicts and prepare
   proposed fixes, but unresolved or unvalidated fixes cannot become ready.
5. Run repository validation on the combined result and expose commands, results,
   and logs. Show source approval/CI separately from combined validation.
6. Present the exact prepared revision and membership for human review. Changes
   to a source head or target branch invalidate readiness and require preparation
   again. A failed refresh must not preserve a misleading ready indicator.
7. A human explicitly publishes the draft through the dashboard, then reviews
   and merges it on GitHub.
   Recheck current revisions and required gates at the action boundary.

## Approved final merge workflow

Publish a dedicated integration branch and combined draft PR.
CI and branch protection apply to the actual combined revision, and the human
merges that PR on GitHub. Keep the original PR links and pinned revisions in its
description. Original PR closure and attribution need explicit handling; do not
claim that merging an aggregate automatically marks each source PR merged.

## Implementation constraints and proposed defaults

Build on the current Python/SQLite application and dashboard. Keep stack records
independent of review jobs and automatic review triggers. Preparation runs in the
background with visible state, bounded concurrency, and restart-safe records.
Never mutate contributor branches, force-push, bypass branch protection, or invoke
merge actions from an agent preparation job. Use current GitHub authorization
and dashboard request protections. Preserve existing review qualification and
author-exclusion rules; stack eligibility must not silently grant review approval.

Preparation should be deterministic git work by default. Any optional agent help
uses the existing subscription-authenticated harness and exposes its proposed
changes for review. No new API credential requirement is proposed.

Builder-check defaults (proposed implementation choices):

- Preparation initially combines commits and reports conflicts; automatic code
  repair is outside this feature. Agents can request preparation and inspect its
  report, but publication and merging remain human actions.
- Local validation commands come from explicit repository configuration, run in
  the existing isolated worker environment, and are recorded with exit status.
  Missing commands mean validation is unconfigured, not passed. A human may
  publish a conflict-free draft integration PR to obtain GitHub CI; only current
  GitHub checks and repository protection determine merge eligibility. Publication
  and merge readiness must therefore be separate states.
- Preserve source commits using merges
  rather than squash/cherry-pick during preparation. Record source PR links and
  authors. After the aggregate merges, refresh GitHub's source PR states and show
  any still-open PRs for human cleanup; do not automatically close them. Make this
  consequence visible before publication, including when the final merge method
  might prevent GitHub from recognizing source commits as merged.

## Required evidence before completion

- Compatible PRs produce a combined revision containing every selected change.
- Individually clean PRs that conflict cumulatively cannot become ready.
- Dependencies, missing prerequisites, cycles, drafts, and missing approval or CI
  evidence receive explicit outcomes.
- Source-head and target movement invalidate prepared evidence and block stale
  publication or merge actions.
- Repeated requests cannot duplicate external writes; failures and interrupted
  preparation remain inspectable and recoverable.
- Preparation cannot invoke a merge. External writes occur only after the
  corresponding explicit human action.
- Dashboard coverage exercises selection, preparation progress, conflicts,
  validation results, stale evidence, and the chosen final action.
- Existing review, priority, publication, and dashboard checks still pass.

## Repository reconnaissance (2026-11-24)

- `agentops/merge_priority.py:rank_prs` already builds prerequisite ordering and
  checks draft, approval, CI, mergeability, and branch freshness. Reuse its
  dependency interpretation, but retain the full inventory for explaining
  ineligible selections: its visible output deliberately filters some PRs.
- `agentops/panel.py:make_server` protects POST actions with a loopback host,
  same-origin check and launch token. Stack actions must use that same boundary;
  never include its token in persisted reports or logs.
- `agentops/ledger.py:Ledger` supplies SQLite transactions and durable review
  records. Stack membership, preparation attempts and publication receipts need
  their own records, rather than disguising an aggregate as a single-PR review.
- `agentops/worker.py:Worker.prepare` is oriented around single-PR review
  provenance. Introduce a distinct preparation path; changing its meaning would
  risk existing review and publication guarantees.
- `agentops.example.toml` explicitly states that merging is never allowed.
  The recommended combined-PR workflow preserves this policy by leaving the
  final merge on GitHub. Choosing dashboard-initiated original-PR batch merges
  would require an explicit policy change in addition to implementation.
- No stack preparation module, stack tests, or prior stack implementation plan
  exists in the current worktree. The existing nested priority display is a
  recommendation, not an integration result.

## Acceptance scenarios for the implementation plan

Use temporary real git repositories for cumulative integration tests. Two PRs
editing different lines in one file should integrate; two PRs replacing the same
line differently should stop at the conflicting member. A dependency chain must
integrate parent first, and a cycle must fail before any external write. Advance
the target or one member after preparation and demonstrate stale publication
rejection. Simulate interruption and an ambiguous publication response; recovery
must inspect the existing branch/PR before retrying an external write.

Use a fake GitHub transport to assert that preparation makes no write calls and
that explicit publication never calls merge, auto-merge, source-branch update,
or branch-protection bypass endpoints. Browser tests should assert the same
distinction in user-visible states and controls. Real GitHub writes are not
required to establish these guarantees during development.
