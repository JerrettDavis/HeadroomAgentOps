# All-sessions dashboard implementation plan

> Execute inline with the executing-plans workflow and request independent review before publishing.

Goal: default the session selector to `<ALL>`, deduplicate PRs by repository/number,
and retain all session content with clear source labels.

Architecture: read all relevant ledger rows in one transaction, associate each job
with its own run snapshot/config, and group only the presentation records. Keep
usage/action accounting over the underlying jobs and attempts. Retrieve grouped
detail artifacts using each job's original number/head and existing integrity checks.

Constraints: preserve loopback/auth/publication guards, read-only reports, exact-head
diff provenance, responsive columns, and explicit single-run filtering. No worker,
automation, database schema, credential, or publication changes.

- [x] Add failing Python regressions for default/explicit all scope, cross-run PR
  grouping, repository separation, attempt totals, source-tagged actions, and grouped
  original-head detail artifacts.
- [x] Implement aggregation and grouped details in `agentops/panel.py`, preserving
  single-run behavior and one read transaction per report.
- [x] Add browser fixture overlap and regressions for `<ALL>` default, one queue row
  per PR, source-labeled detail tabs, session usage, and single-run selection.
- [x] Update `agentops/panel.html` to render the grouped records and preserve selection
  across newly observed sessions. Label comments, active agents, and session spend.
- [x] Verify existing Python/browser tests, review independently, then update the live
  panel without interrupting workers. Commit/push and verify CI.

Review focus: unrelated repositories with matching PR numbers; stopped/empty runs;
older and newer heads for the same PR; multiple attempts and reused executions;
session labels and selected PR remaining correct during background refresh.
