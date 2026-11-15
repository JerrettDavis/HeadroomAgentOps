# Provider Allowance Limits Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show account allowance usage for Codex and other configured providers and stop new reviews at a configurable percentage used (default 80%).

**Architecture:** Read native account allowance data without starting inference. Persist only normalized allowance windows and shared stop settings in the ledger. All coordinators consult the same gate before dispatch; running reviews continue monitoring and publication while queued work waits and resumes automatically.

**Tech Stack:** Python standard library, SQLite, existing HTML/JavaScript dashboard and unittest/Playwright suites.

**Spec:** The user requests weekly provider limits on the dashboard and a configurable stop point, for example 80%. Defaults and failure behavior below are implementation decisions.

## Global Constraints

- Preserve the running service and workers; never merge. Existing author exclusions and publication qualification remain.
- Subscription authentication only for Codex. No inference calls to measure account usage. Never persist or display credentials or raw provider errors.
- Default stop is 80% used, inclusive, for applicable allowance windows. Display weekly, short and monthly windows truthfully with reset times.
- When enforcement is on, missing, failed, stale or expired allowance observations pause new work; unknown is never zero. An explicit enforcement override is visible in the UI.
- Pausing preserves queued work and allows running reviews to finish. Settings affect saved plans and concurrent coordinators, and survive restart.
- Cache account readings for at most 60 seconds, refresh after reset, coordinate refresh across processes, and keep dashboard requests responsive.

## Review Focus

- Account changes and distinct credential roots cannot share cached usage.
- Expired reset windows cannot imply a fresh empty allowance.
- Multiple coordinators cannot race refreshes or bypass a changed threshold.
- Provider failures must neither leak credentials nor become false zero readings.
- Quota pauses cannot stop publication, consume retries, or turn queued jobs into failures.

### Task 1: Native allowance sources and shared gate

**Files:** create `agentops/allowances.py`, `agentops/provider_usage.py`, `tests/test_allowances.py`, `tests/test_provider_usage.py`.

- [x] Write failing tests for inclusive thresholds, persisted settings, multiple windows/providers, malformed/unavailable data, resets, freshness, account separation and concurrent refresh.
- [x] Verify failures with `python -m unittest discover -s tests -p 'test_*usage.py'` and `python -m unittest discover -s tests -p test_allowances.py`.
- [x] Implement normalized provider window readers and `Allowances(ledger, config).check()` / `.status()` / `.configure()` with injected readers for offline tests. Use Codex app-server `account/rateLimits/read`; verify Claude/Copilot sources before enabling them. Whitelist saved fields.
- [x] Run focused tests and live read-only Codex smoke check.

### Task 2: Dispatch enforcement and automatic resumption

**Files:** modify `agentops/coordinator.py`, `agentops/cli.py`, `agentops/ledger.py`, and coordinator/launcher tests.

- [x] Write failing tests proving queued work waits without consuming attempts, active workers finish/publish, reset/settings changes resume dispatch, and the last pre-start check defers safely.
- [x] Add shared allowance gate to actual coordinator dispatch, covering CLI, manual launch, automatic launch, retries and resumed saved plans. Clean staged auth if a late pause occurs.
- [x] Verify focused coordinator and launcher tests with fake providers; no network or model calls in tests.

### Task 3: Dashboard, validation and deployment

**Files:** modify `agentops/panel.py`, `agentops/panel.html`, panel/API and Playwright tests; document `docs/provider-limits.md`.

- [x] Add protected persistent allowance settings API and asynchronous status refresh; test origin/token protection, validation and responsive failures.
- [x] Add branded allowance progress, reset/freshness, paused reasons, threshold and explicit override controls. Test boundary pause, unknown state, persistence, keyboard access and narrow viewports.
- [x] Run complete Python/UI/security checks and independent review, resolve findings, commit and push. Verify CI.
- [x] Deploy panel and ensure all running coordinators use the guard without interrupting models; verify real weekly usage and queue state. Leave service running.

## Execution notes

- Implemented inline under the existing authorization to complete, test, commit and push.
- Ruling: missing weekly data pauses new dispatch even when a monthly entitlement is known; this avoids authorizing unknown weekly consumption.
- Ruling: existing workers finish and publish during allowance/settings failures; queued work does not consume an attempt.
- Independent review cleared account/source/staging binding, authentication policy, UI draft preservation and missing-window handling.
- Verified real Codex CLI0.167.1 account-bound weekly reading at61%; Copilot exposes monthly25.6% and no weekly window; Claude has no host subscription credentials.

- Completion evidence: implementation cd53202 passed all13 GitHub checks, including Docker e2e, six OS/Python combinations, UI, packaging and security. Live API threshold61 paused; restored80 allowed; panelPID129116. A fresh PR4877 worker was observed running under the deployed guard. Final metadata/test follow-up is verified separately before goal completion.
