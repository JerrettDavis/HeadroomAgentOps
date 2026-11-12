# Dashboard review launch implementation plan

**Goal:** Launch individual, batch, and query-selected PR reviews from the panel.
**Architecture:** A launch service resolves selections, stores preview receipts,
creates existing ledger plans, and starts the CLI coordinator. The existing
panel serves the wizard and protected POST routes.
**Stack:** Python standard library, SQLite, existing HTML/CSS/JavaScript.
**Spec:** `docs/superpowers/specs/2026-11-11-review-launch-design.md`.

- [x] Add selection/preview/launch service and tests for invalid selections,
  complete query pagination, stale heads, subprocess arguments and idempotency.
- [x] Extend panel routes and CLI configuration plumbing; test same-origin and
  token checks, and ensure GET routes retain read-only behavior.
- [x] Add an accessible wizard with settings, selection checkboxes, launch state,
  and errors; document operation and verify the actual browser flow.
- [ ] Run the suite, inspect the diff, push and land on origin/main, then verify
  the remote commit and required checks.
