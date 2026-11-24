# PR Auto-Stacker Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Native execution minimizes agent overhead.

**Goal:** Prepare compatible PRs deterministically and publish one combined draft PR on explicit human action, leaving merging to humans on GitHub.
**Architecture:** A separate SQLite stack store records pinned membership and attempts. A deterministic planner orders dependencies, a Git engine integrates immutable commits, and a bounded service coordinates preparation, validation, stale checks and idempotent publication. The existing dashboard exposes these operations through its protected POST routes.
**Tech Stack:** Python 3.12+, SQLite, Git, existing Docker worker image, plain browser JavaScript.
**Spec:** `docs/superpowers/specs/2026-11-24-pr-auto-stacker-design.md` (approved 2026-11-24).

## Global Constraints

- No model calls, agent judgement, or new runtime dependencies in preparation.
- No merging, auto-merge, force-push, contributor-branch writes or protection bypass.
- Capture source approvals/CI separately from validation of the combined revision.
- Commands execute only in isolated containers without coordinator credentials.
- Unknown/stale evidence fails closed. Publication is a separate human action.
- Reuse pinned results; use stable dependency ordering and bounded work.

## Review Focus

- Individually mergeable PRs conflict cumulatively: real Git integration tests.
- Branch or approval changes between preparation and publication: fresh action-boundary tests.
- Ambiguous external writes and interruption: receipt reconciliation and restart tests.
- Validation mutates checkout or fails: immutable publication and validation-state tests.
- Untrusted titles, errors, or URLs: browser escaping and narrow-layout tests.

### Task 1: Deterministic planning and durable state

**Files:** Create `agentops/stacking.py`, `tests/test_stacking.py`.
**Interfaces:** `plan_stack(inventory, numbers)` returns pinned ordered members and blockers. `StackStore(path)` supplies create/get/list/update/claim backed by a separate table in the existing SQLite file.

- [x] Write tests for stable ordering, prerequisite inclusion, cycles, missing evidence, invalid selection and idempotent pinned identity; run and observe missing implementation failure.
- [x] Implement the planner using existing dependency parsing and stable topological ordering; persist records with transactional compare-and-set transitions and process ownership.
- [x] Run `python -m unittest discover -s tests -p test_stacking.py`; expected PASS. Commit task.

### Task 2: Git integration and isolated validation

**Files:** Create `agentops/stack_git.py`, `tests/test_stack_git.py`.
**Interfaces:** `GitStack(root, repo, ...)` exposes `prepare(record, progress)` returning revision, member results, conflicts and validation. `publish_branch(record)` writes only its dedicated branch after verifying the exact prepared revision.

- [x] Write real temporary-Git tests for compatible edits, cumulative conflicts, pinned-head mismatch, dependency ancestry, repeat reuse, and source-commit preservation; observe failure.
- [x] Implement a private checkout, batched ref fetch, SHA verification and hook-free sequential merges with deterministic commit metadata. Preserve the pristine integration checkout; run configured argv commands in a separate no-network Docker container without auth, with bounded logs and execution time. Missing commands remain unconfigured.
- [x] Test Docker argument isolation, failure and timeout outcomes, and ensure validation changes never enter the published Git revision.
- [x] Run `python -m unittest discover -s tests -p 'test_stack_git.py'`; expected PASS. Commit task.

### Task 3: Preparation service and guarded publication

**Files:** Create `agentops/stack_service.py`, `tests/test_stack_service.py`; modify `agentops/merge_priority.py` to expose optional full normalized inventory without changing its default response.
**Interfaces:** `StackService(path, config, github, ...)` exposes `prepare(request)`, `publish(request)`, and `status()`; consumes Task 1 records and Task 2 Git engine.

- [x] Write tests for durable background progress, bounded concurrent preparations, repeat requests, stale head/base/approval/CI, failed refresh, publish-disabled policy, existing-branch/PR recovery and interrupted owners; observe failure.
- [x] Implement non-model preparation with one active operation, fresh inventory checks at publication, dedicated branch ownership verification and paginated PR discovery by head. Persist publication intent before writes; reconcile ambiguous results on retry. Always create draft PRs and never merge. Refresh published aggregate/source state on explicit request.
- [x] Run `python -m unittest discover -s tests -p test_stack_service.py`; expected PASS. Commit task.

### Task 4: Dashboard, API and operator documentation

**Files:** Modify `agentops/panel.py`, `agentops/panel.html`, `agentops/ui/merge-priority.js`; create `agentops/ui/stacking.js`, `tests/ui/stacking.spec.cjs`, `tests/test_stack_panel.py`, `docs/pr-auto-stacker.md`; update `agentops.example.toml`.
**Interfaces:** GET `/api/stacks`; protected POST `/api/stacks/prepare`, `/api/stacks/publish`, `/api/stacks/refresh`. Browser selection submits integer PR numbers; server determines dependencies, evidence and identities.

- [x] Write API/browser tests for human-only publication, selection with prerequisites, progress/conflicts/logs, freshness failures and safe links/text; observe failures.
- [x] Add selection controls to priority view and a durable stack panel with explicit Prepare and Publish draft PR buttons, pinned members, validation logs and GitHub merge link. Keep existing layout/style and keyboard behavior.
- [x] Document configuration, algorithm, validation limits, draft publication, source PR cleanup, recovery and zero model use.
- [x] Run complete Python suite and Playwright suite; expected PASS (document explicit environment skips). Obtain whole-branch review, resolve findings, and commit.

Final evidence and plan adjustments: `docs/superpowers/specs/2026-11-24-pr-auto-stacker-verification.md`. Final source base is eb4ccdc; 409 Python tests (Docker enabled, no skips) and 30 browser tests pass.
