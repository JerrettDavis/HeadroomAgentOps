# Material review implementation plan

**Goal:** Avoid agent work for routine PR updates while retaining durable review context and explicit re-review support.

**Approved behavior:** Skip verified unchanged PR diffs, formatting-only changes and dependency-only updates; persist decisions and prior context; explicit re-review overrides skips. Material updates receive the new diff and prior notes.

**Architecture:** Deterministic comparison of complete PR file evidence before automatic dispatch. Store baseline and skip receipts in existing evidence storage. Keep review reuse separate from approval and CI qualification.

**Constraints:** Preserve author exclusion, subscription Codex, qualification gates, and Dependabot fix-only policy. Unknown or incomplete evidence requires review. No generic whitespace stripping that hides strings or indentation. Main updates/conflict resolutions qualify only when the resulting PR contribution is equivalent.

- [x] Add tested material diff classifier (unchanged patch independent of hunk positions, dependency-only updates, conservative whitespace equivalence, missing patches, renames and mixed updates).
- [x] Integrate before automatic worker dispatch, validate current head, persist baseline/skip receipts and explicit re-review override.
- [x] Stage additive changed-file context and durable prior notes for material updates; retain complete evidence for qualification.
- [x] Run scheduler/context/regression tests, independently review, deploy and verify live polling and worker behavior.

## Initial evidence, 2026-11-18 17:54 CST

Clean main checkout at 62fe7e1. Live panel PID 322860 uses worktree C:/Users/jd/.codex-hr/worktrees/5fb1/HeadroomAgentOps and shared G:/git/HeadroomAgentOps/.agentops. API confirms Codex selected, automatic new/updated reviews enabled, zero eligible jobs, no active worker processes, seven qualification-held PRs, no reconciliation errors. Coverage reuse currently requires identical head/base/main; automation schedules by head change.

## Implementation rulings and review

- Manual New Review already launches with --force-fresh, so automatic filtering does not intercept explicit re-reviews.
- Whitespace equivalence requires full hash-verified Python source; preserve literals, indentation and logical newlines. Other unknown whitespace changes require review.
- Moving hunk positions requires complete merge-base content alignment, preserving function/context anchors. Repeated code cannot be matched by patch text alone.
- Only dependency fields in supported manifests and modified lockfiles are exempt. Mixed source changes, scripts and build configuration remain material.
- Stale completed analysis remains usable as context, never as current approval or CI evidence. Compare cumulative updates against actual prior analysis, never advance the baseline on a skip.
- Independent reviewer reproduced logical-newline, relocated-edit, cached-retarget and target-tip/merge-base defects; regression tests and fixes address all findings. Final canonical verification: `python -m unittest discover -s tests -q`, 357 tests, OK with 4 Docker skips. Independent review found no remaining important defects.

## Live rollout, 2026-11-18

After the user explicitly authorized restart, replaced idle panel PID 322860 with PID 323564 using the clean runtime `.worktrees/material-review-live` at d1295b4 and the existing Codex config/shared state. Restored both automatic triggers, publication and concurrency four. Verified multiple fresh inventory polls, zero scheduler/reconciliation errors, Codex allowance available at 2% weekly usage, and no browser console errors. Reviews layout renders correctly; merge priority returned ten approved PRs, with background refresh/freshness gating preserved.

There are zero eligible reviews and seven qualification-held PRs; Codex is ready rather than consuming tokens on unnecessary reruns. An obsolete #4331 claim had a missing coordinator, exited worker and stale revision. Recovered it through `Ledger.recover` to interrupted, preserving all output with no model rerun or publication; the ledger now has zero falsely running jobs. Deployment and recovery receipts persist in `.agentops/material-review-deployment.json` and `.agentops/material-review-orphan-recovery.json`.
