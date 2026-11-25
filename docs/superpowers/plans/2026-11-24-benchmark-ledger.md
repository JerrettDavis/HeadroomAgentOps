# Benchmark execution ledger

User authorized autopilot and automatic plan approval. Worktree:
`G:/git/HeadroomAgentOps/.worktrees/deterministic-benchmarks`, branch
`feature/deterministic-benchmarks`, base/main57b59bf. Fetched origin/main again;
no advancement. Review services untouched; final commit and draft PR follow validation.

## Implemented

Pinned upstream234c233cc439aedba53b242f02f695de5f90c877, ADR0003/0004,
PR3636–3639 and benchmark/common schemas. Same verified candidate proxy for A1
(optimization disabled) and B(enabled). Seeded paired order/repeats, observed
identities/rollout/resources, versioned paired-t95 policy, absolute accuracy floor,
missing-data/overflow/drift guards; CLI inspect/init/plan/run/collect/reference;
OpenAI/Azure and Anthropic/Bedrock, streaming/tool assertions; isolated fresh venv,
homes/imports; package/interpreter fingerprints; Docker/SSH; hashed evidence;
upstream refs; pinned Docker base; docs/examples; CI and manual OIDC publication.

Windows actual-wheel testing required UTF8, named job wrapper assigned before
candidate code, controller sole handle ownership, wait for all job members, and
bounded owned Workspace cleanup retries. Parent/name checked before deletion.

Durable Docker cancellation added docker_transport.py, private OUTPUT/.docker-UUID,
named container, docker-receipt,60s stop only on explicit cancellation, collection
without restart. SSH cancellation signals only receipted PID after /proc cmdline
verification. Collection rejects stale local summaries and checks fresh raw batch,
actual byte SHA/canonical JSON and experiment binding. Cancellation derives a new
immutable inconclusive result. Inputs/receipts retained for recovery.

## Observed verification

* unittest discovery:410tests,6skips,pass.
* Latest default benchmark command `python -m pytest tests -q -k benchmark`:
  73passed,4opt-in skips,410deselected (before pending exit-state fix below).
* Real Docker + Linux SSH tests, including active-request cancellation and
  idempotent collection:2passed149.51s. Enable HEADROOM_BENCH_DOCKER_IMAGE=
  headroom-benchmark:validation and HEADROOM_BENCH_SSH_IMAGE=
  headroom-benchmark-ssh:validation.
* Actual published0.45.0 Windows wheel full CLI inspect/install/run against local
  provider:passed386.60s,ten paired attempts,simulator lane. Wheel under
  .validation/candidate, SHA2569ddcf12e6a70c73525783a7db31b7261a612215e47dcedec2e94d72260aa2648.
* Actual Azure v1 routing + Bedrock SigV4 Converse assertions passed using the same
  verified retained installation (debug factory avoided repeated downloads).
  Fixed ambient HEADROOM_BACKEND overriding explicit --backend by setting child
  env too; regression covers both flag syntaxes. Azure api-key and /openai/v1/chat/
  completions verified. Bedrock local /model/.../converse signature verified.
  Fixture needs AWS_BEDROCK_RUNTIME_ENDPOINT and AWS_ENDPOINT_URL(_BEDROCK/
  _BEDROCK_RUNTIME); BEDROCK_TARGET_API_URL controls direct passthrough only.
  Response model is original requested Bedrock ID. Initial fixture debug with
  fake credentials hit AWS auth/control-plane errors before endpoint correction;
  no real provider credentials or paid completions used.
* Ruff0.17.3 check/format, Actionlint both workflows, uv lock check passed.
  Wheel/sdist + Twine6.2 + inclusion/private-data checks passed before latest edits;
  rebuild at finish. MANIFEST explicitly allows examples, not arbitrary JSON.
  Windows pytest glob fixed to `python -m pytest tests -q -k benchmark`.

## RESOLVED REVIEW FINDING

The Docker OOM/exit137 qualification defect is resolved. Collection now persists
verified terminal proof before stopped-container removal, binds it into hashed
results, and supports idempotent recollection. SSH supervises a separate CLI
child and records its actual exit code; missing terminal proof is inconclusive.
Independent targeted review found no remaining blockers (24 focused tests).
Fresh real Docker/SSH integration checks passed: 2 tests in 145.25 seconds,
including active-request interruption and repeat collection.

## Final requirement verification

Fresh root unittest regression: 410 tests passed, 6 skipped (68.087s).

- Deterministic paired experiments and fail-closed policy: 83 native benchmark
  tests passed; 4 optional transport/actual-wheel tests skipped in that invocation.
- Docker and remote execution: fresh real Docker/Linux SSH integration run,
  2 passed in 145.25s, including cancellation and idempotent collection.
- Published candidate compatibility: Headroom 0.45.0 Windows install/inspect/run
  previously passed against a local provider (simulator lane); Azure routing and
  Bedrock SigV4 Converse assertions passed with the verified installation.
- Upstream contracts and exit-proof regressions: covered by native suite and
  independent targeted review (24 focused checks passed; no remaining blockers).
- Distribution and automation: Ruff check/format, Actionlint, uv lock check,
  wheel/sdist build and Twine 6.2 checks passed. Both archives include the CLI
  and exclude private .validation/.remember/.agentops/.venv directories.
- Live cloud provisioning and paid provider-performance measurements were not
  performed. No real provider credentials or paid model completions were used.

Private .validation staging is ignored and must never be published.
Autopilot authorizes feature commit and draft PR; do not merge or restart services.
