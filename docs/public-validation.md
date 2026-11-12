# Validation scope

The local suite passed all 157 tests with real Docker integration enabled on
2026-11-11. Docker cases use deterministic fake harnesses rather than live model
accounts. They cover candidate archiving, nonzero exit, malformed output and
timeout after a candidate commit. Publication tests use local Git repositories.
They do not prove hosted upstream publication or live-provider acceptance.

Workflow syntax passed Actionlint. Source and wheel distributions passed Twine
checks, and an isolated wheel installation ran the CLI outside the source tree.
Python and worker npm dependency audits reported no known vulnerabilities at
validation time. See the hosted Actions results for the current commit and
supported platform matrix.

The panel reads persisted local ledger data; it does not provide fresh GitHub
qualification. Missing evidence stays unknown. Workflow provenance, baseline
exceptions and live-provider proof can still hold publication. No automated
merge capability exists. Live model tests require operator credentials and
explicitly selected target repositories; CI never uses those credentials.

Private operational ledgers, run receipts, model authentication, candidate
archives and internal planning/history are intentionally excluded from the
public source snapshot. Public source and tests contain only implementation,
synthetic fixtures and general operating/security guidance.
