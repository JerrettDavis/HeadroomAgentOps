# Dashboard Maintenance Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task by task with test-first changes and a fresh whole-branch review.

**Goal:** Ship bulk PR rebases and optional conflict remediation, plus persistent main CI monitoring and agent repair PRs.

**Architecture:** Repository-scoped SQLite maintenance records own claims, receipts, settings and incidents. Separate Git/GitHub inventory and mutations from background orchestration, reuse credential-isolated harness execution, and expose authenticated panel actions through a dedicated UI module.

**Tech Stack:** Python 3.12+, SQLite, Git/GitHub CLI, existing Docker worker/harness adapters, vanilla JavaScript and Playwright.

**Spec:** `docs/superpowers/specs/2026-11-26-dashboard-maintenance-design.md` (approved 2026-11-26).

## Global Constraints

- Preserve queues, auth roots, harness choices, publication/CI gates, author exclusions and both watches.
- Never print credentials, tokens or raw production command lines.
- Use guarded reloads, not stop, to preserve live queued work.
- Actual rebases use observed-head leases; uncertain mutation outcomes require reconciliation.
- Mechanical updates produce no extra reviews; substantive agent edits retain normal review behavior.
- Main repair opens a PR for human review and never merges it automatically.
- Keep current Visual Benchmark Studio functionality during integration and relaunch.

## Review Focus

- Concurrent author pushes and main movement must never be overwritten or hidden.
- Late workflow creation and changed execution sources must not bypass approval provenance.
- Restart or network ambiguity must not duplicate pushes, agent execution or repair PRs.
- Untrusted repository files/logs must not reach host credentials through agent execution.
- Incomplete workflow visibility must never clear a broken-main incident.

## Task 1: Integration baseline and durable maintenance state

**Files:** `agentops/maintenance_store.py`, `tests/test_maintenance_store.py`; preserve integration with `feature/visual-benchmark-studio`.
**Interfaces:** `MaintenanceStore(path, repo)` supplies `settings()`, `configure(values)`, `put(kind, key, data)`, `get(kind, key)`, `list(kind)`, `claim(kind, key, data)` and `update(kind, key, data)`; claims are atomic and repository scoped.

- [ ] Integrate deployed Studio branch, establish isolated dependencies and run baseline suite.
- [ ] Write failing tests for persisted settings, atomic duplicate claims, repository isolation and updates; run with `python -m unittest discover -s tests -p test_maintenance_store.py -v` (expected missing implementation).
- [ ] Implement SQLite state and bounded public records; rerun the same tests (expected PASS) and commit.

## Task 2: PR drift, rebasing and workflow continuity

**Files:** `agentops/maintenance_git.py`, `agentops/maintenance_github.py`, `tests/test_maintenance_git.py`, `tests/test_maintenance_github.py`.
**Interfaces:** `MaintenanceGit.prepare(record)` returns pinned checkout, old/new heads and contribution attestation; `publish(record)` enforces remote head lease. `MaintenanceGit` uses the existing safe Git configuration. GitHub inventory enumerates all open main PRs; approval continuation binds prior approval evidence to exact source bytes and new run heads.

- [ ] Add failing real Git fixture tests for successful rebase, conflicts, changed-head lease rejection and contribution equality; add inventory pagination and approval history/source tests. Run named modules (expected FAIL).
- [ ] Implement pinned rebase preparation/publication and exact-source workflow continuations; run named modules (expected PASS) and commit.

## Task 3: Durable orchestration and agent repair execution

**Files:** `agentops/maintenance_service.py`, `agentops/maintenance_agent.py`, `agentops/automation.py`, associated `tests/test_maintenance_*.py`.
**Interfaces:** `MaintenanceService(path, config, github, ...)` exposes `status()`, `refresh()`, `update_selected(request)`, `remediate(request)`, `configure(request)`, `fix_main(request)`, `tick()`, `run()` and `close()`. Agent executor uses configured harness, credential-free repository execution and host-only guarded publication. Store task/incident records persist remote receipts and reconciliation markers.

- [ ] Add failing tests for mixed batches, duplicates, delayed approvals, restart, substantive review behavior, latest-attempt CI classification, incident deduplication/resolution and autofix persistence; run focused maintenance modules (expected FAIL).
- [ ] Implement background service, monitor and real configured-harness repair-to-PR flow with controlled fixture lifecycle tests. Ensure mechanical suppression verifies attestation rather than trusting a label. Run focused suites (expected PASS) and commit.

## Task 4: Dashboard controls and lifecycle integration

**Files:** `agentops/panel.py`, `agentops/panel.html`, `agentops/ui/maintenance.js`, `agentops/ui/maintenance.css`, `tests/test_maintenance_panel.py`, `tests/ui/maintenance.spec.cjs`.
**Interfaces:** authenticated `/api/maintenance/*` POST actions and read-only `/api/maintenance` status. Panel owns independent service thread and clean shutdown. UI displays all drift rows, selection, durable secondary queue, agent action, main incident alert/settings/repair links.

- [ ] Write failing API and browser tests for authorization, selections, partial outcomes, remediation and main monitor/alert/autofix controls; run them (expected FAIL).
- [ ] Implement matching controls with accessible labels and safe DOM rendering; run API and browser tests (expected PASS) and commit.

## Task 5: Review, land, relaunch and acceptance

**Files:** acceptance evidence in `docs/superpowers/plans/2026-11-26-dashboard-maintenance-acceptance.md` and ledger.

- [ ] Run full Python and browser suites plus real Git and Docker fixture execution. Record exact commands and results.
- [ ] Obtain fresh independent whole-branch review; resolve important findings with regression tests.
- [ ] Push feature branch, create reviewable PR, inspect remote CI and land on main when green under existing repository policy.
- [ ] Verify landed main SHA, snapshot runtime invariants, use guarded reload to immutable main checkout when idle, and verify invariants and rendered dashboard afterward.
- [ ] Audit every approved spec requirement against authoritative evidence and provide user URLs and final checks. Do not mark complete while any required deployment/validation remains.
