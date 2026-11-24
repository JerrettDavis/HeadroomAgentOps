# PR auto-stacker verification

Verified 2026-11-24 on `feature/pr-auto-stacker`, based on source commit
`eb4ccdc292f3f1d843bee08a540b76208e7cb619`. The managed worktree initially used
an older default-branch commit; all feature commits were rebased onto the actual
source branch before the final verification. The final diff preserves existing
review, qualification, provider, publication and worker behavior.

## Requirement evidence

- Algorithmic preparation with zero model calls: `stacking.py` performs bounded
  graph closure and heap-based topological ordering; `stack_git.py` performs
  batched Git fetching and cumulative merges. Neither imports or invokes a model
  harness. `test_stacking.py` proves stable identities, prerequisite inclusion,
  cycles, gate failures and invalid input handling.
- Actual combined compatibility and source attribution: `test_stack_git.py`
  integrates edits in the same file, verifies each source head is an ancestor,
  checks reproducible integration SHAs, and reports a conflicting PR and path.
  It rejects changed fetched heads and verifies that preparation leaves the
  upstream target unchanged.
- Durable and bounded work: transactional records and atomic preparation
  ownership live in `StackStore`. `test_stack_service.py` covers persistence,
  same-input reuse, competing panels, dead and unknown owners, and recovery.
  The reviewer's ownership-race finding was reproduced with a failing test and
  fixed by acquiring ownership and applying planning evidence in one transaction.
- Isolated validation: real `test_stack_docker.py` tests execute without model or
  GitHub credentials or network routes, verify Git access through the Windows
  bind mount, isolate checkout edits, and exercise timeout failure and cleanup.
  Process tests prove bounded output retention. Missing validation is explicitly
  unconfigured; failures cannot become publishable.
- Human publication and fresh evidence: service tests prove preparation performs
  no GitHub writes, publication creates only a draft PR, and changed source heads,
  target, approvals, CI or unavailable inventory block publication. A different
  existing branch revision is rejected. No merge or auto-merge path exists.
- Idempotence and recovery: a lost create-PR response is reconciled without a
  second PR or push. Rechecking recovers the existing aggregate even after target
  movement. Published aggregate/source refresh lists still-open source PRs for
  human cleanup without closing them.
- Request protections and read-only browsing: `test_stack_panel.py` exercises
  same-origin/token requirements for all three actions. Existing panel/priority
  tests and the new read-only peek test prove ordinary browsing does not create
  or mutate the ledger. Service initialization is lazy.
- Dashboard behavior: `tests/ui/stacking.spec.cjs` covers selection, separate
  preparation/publication clicks, saved state, GitHub links, conflicts, stale
  controls, validation status, escaped untrusted content and narrow layout.
- Operator guidance: `docs/pr-auto-stacker.md`, `agentops.example.toml` and README
  explain command configuration, no-model operation, GitHub final merging,
  freshness, source cleanup and recovery.

## Final checks

- `AGENTOPS_DOCKER_TESTS=1 python -m unittest discover -s tests`: **409 passed,
  no skips**, including real worker and stack validation containers.
- Complete Playwright suite with `tests/ui/playwright.config.cjs`: **30 passed**.
- `git diff eb4ccdc --check`: clean.
- Independent review confirmed the ownership fix and reviewed the rebased diff;
  no remaining concrete findings.

No live services were restarted, and no real GitHub PR, branch, review, merge or
source closure was performed for this feature's verification. Development used
temporary Git repositories, deterministic transport fixtures, and local Docker.

Plan adjustments: HTTP coverage lives in the focused `test_stack_panel.py`
instead of extending `test_panel.py`; browser tests reuse the existing fixture
without changing `tests/ui/serve.py`. Native PowerShell bookkeeping replaces the
skill's Bash-only helper scripts. These do not change the approved behavior.
