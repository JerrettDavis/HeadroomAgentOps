# Execution ledger — 2026-11-26-dashboard-maintenance.md

- Design approved by user 2026-11-26 09:59 CST. Full landing and relaunch authorization retained.
- Baseline attempt on main d0227a2: unittest ran 433 tests, 15 module import errors for missing pytest/jsonschema, 6 skips. Environment mismatch, not accepted as green.
- Ruling: integrate deployed Studio branch before implementation — preserve existing runtime functionality on requested main landing — costs broader integration diff, verified by full tests and remote CI.
- Pre-flight: tasks 2–4 consume task 1 persistence; task 3 consumes task 2 immutable receipts; task 4 consumes task 3 service API. Signatures coordinated in plan, no known conflict.
- Execution: inline implementation with one independent final reviewer; no further implementation approval is needed after user's design approval.
- Task 1: complete — merge bd30ca1 preserves Studio; isolated `uv sync --frozen --extra benchmark-test`; baseline pytest 702 passed, 21 skipped, 71 subtests; persistence tests 3 passed (4a43ad6).
- Task 2: complete — six Git/GitHub tests passed, including real lease rejection and conflicts (5fcab59).
- Task 3: in progress — service/store 8 passed; agent contract/service 8 passed; workflow/automation 34 passed, 3 subtests. Root cause of regression was Launcher exposes `root`, not `path`; corrected hook uses existing ledger convention.
