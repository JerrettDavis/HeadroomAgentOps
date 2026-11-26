# Visual Benchmark Studio Implementation Plan

**Goal:** Implement the approved visual benchmarking workflow, including source discovery, runnable templates, experiment design, execution and evidence exploration.
**Architecture:** Extend the existing private panel API and durable benchmark service. Keep catalogues, source resolution, experiment groups and presentation separate; reuse native paired evidence contracts.
**Tech stack:** Python standard library, existing benchmark runners, JavaScript/CSS, pytest and Playwright.
**Spec:** `docs/superpowers/specs/2026-11-25-visual-benchmark-studio-design.md`

## Constraints

Preserve existing CLI and job APIs, private panel authentication, simulator labeling, hashed evidence, and credential redaction. Work in the isolated feature worktree; leave live services unchanged while developing. Implement inline using existing execution authorization.

## Review focus

Validate matrix bounds before spawning work; reject unsupported combinations rather than silently omit cells. Never accept a simulator as live evidence. Source searches must not execute builds. Unknown or expired sources must never fall back to HEAD. Reload and cancellation must preserve immutable experiment identity.

## Tasks

### 1. Bundled catalogue and real simulator execution

Files: `agentops/benchmark_studio.py`, `agentops/benchmarks/demo.py`, `tests/test_benchmark_studio.py`, package data as needed.

- [ ] Write and run failing tests for template previews, bounded configuration, deterministic paired simulator evidence, and persisted provenance.
- [ ] Implement `catalogue() -> dict`, `validate_design(payload) -> dict`, and a credential-free demo using `run_experiment`, canonical digests and terminal evidence.
- [ ] Verify fresh-install execution without external paths or credentials; simulator decisions remain inconclusive for live qualification.

### 2. Durable Studio API and groups

Files: `agentops/benchmark_studio.py`, `agentops/panel.py`, `tests/test_panel_benchmark.py`, `tests/test_benchmark_studio.py`.

- [ ] Write and run failing tests for draft round trips, group identities, duplicate launch prevention, recovery, cancellation and partial failures.
- [ ] Add private catalogue/draft/preflight/group/result routes and persist versioned manifests and child states atomically. Reuse existing service jobs for measured native cells.
- [ ] Verify API authentication, response redaction, manifest digests and active-job recovery with real subprocesses.

### 3. Visual landing page, designer and results

Files: `agentops/ui/benchmark-studio.js`, `agentops/ui/benchmarking.css`, `agentops/panel.html`, `tests/ui/benchmark-studio.spec.cjs`.

- [ ] Write and run failing browser tests for one-click demo, template editing, keyboard steps, restored drafts and accessible metric tables.
- [ ] Add catalogue cards, embedded wizard, source inputs, capability-aware options, bounded matrix preview, preflight/review, live jobs and evidence-derived charts. Retain advanced existing forms.
- [ ] Verify desktop/mobile rendering, actual demo results and reload behavior.

### 4. Immutable source discovery and resolution

Files: `agentops/benchmark_sources.py`, Studio API/UI and `tests/test_benchmark_sources.py`.

- [ ] Write and run failing tests for SHA/message/author/PR queries, bounded pagination/cache, stale client responses, artifact expiry/hash/archive validation and build failures.
- [ ] Implement repository-scoped commit search and GitHub Actions artifact discovery, managed verified downloads and explicit pinned wheel builds. Persist provenance and surface actionable errors.
- [ ] Verify real repository search and selected-source execution without mutating the working checkout.

### 5. Measured tools and complete experiment exploration

Files: `agentops/benchmark_tools.py`, native two-build runner contract, Studio API/UI, relevant benchmark and browser tests.

- [ ] Write and run failing tests for native two-build execution, Inspect/AIPerf execution adapters, feature compatibility, matrix cell completeness and tool-specific metrics.
- [ ] Implement configured provider/model capabilities, schema-derived feature settings, measured templates, runner preflight, bounded matrix orchestration and evidence readers.
- [ ] Add distributions, uncertainty, scenario filtering, comparisons, retries, duplication and exports with accessible tables.
- [ ] Run full Python/UI regressions, review the branch independently, and audit every spec requirement against source/runtime evidence. Record unavailable live prerequisites explicitly; do not mark the overall goal complete without required proof.

## Progress

Design approved. Baseline and task 1 implementation next. All five tasks are required for completion.

2026-11-25 implementation ledger:

- Task 1 implemented: three bundled editable simulator templates; strict bounded design validation; actual paired runner/evaluator with hashed terminal evidence in supervised subprocess jobs. No provider calls or external inputs. Simulator decisions remain inconclusive for live qualification.
- Task 2 partial: authenticated state/preview/draft/result routes, validated durable drafts, verified evidence loading. Full experiment groups and measured-cell orchestration remain outstanding.
- Task 3 partial: template cards, four-stage embedded designer, auto-saved navigation, matrix review, live job history, cancellation, paired charts/tables, duplication and evidence export. Measured configuration controls, distributions and cross-run exploration remain outstanding.
- Task 4 partial: cached repository-scoped GitHub SHA/message/author/PR searches, debounced abortable keyboard selector and pinned artifact metadata/expiry discovery. Downloads, verification, explicit builds and connecting resolved sources to execution remain outstanding.
- Task 5 outstanding. All future scope remains required; this slice does not complete the full objective.
- Baseline: 40 existing core/dashboard tests passed. Added red/green backend and real-panel browser tests. Live GitHub search verified for partial SHA, message, author and PR #32 in JerrettDavis/HeadroomAgentOps.
- Independent slice review found query scope escape; reproduced with failing tests, rejected scope qualifiers, and checked returned repository identities. Cache expiry/bounds and foreign-result checks added.
- Ruling: preserve existing file-based UI while adding Studio above it. Adjust existing geometry assertion to account for normal scroll offsets, retaining layout dimensions and tooltip viewport checks. Focused tooltips now reposition on scroll instead of disappearing.
- Verification checkpoint: full Python regression run passed 549 tests with 12 skips and 71 subtests; subsequently added source-scope/cache/provenance and panel authentication cases pass in the focused 32-test set. All 48 browser tests pass. Desktop/mobile result screenshots inspected; chart labels changed to fixed readable text with responsive bars. Independent review cleared the scope fix.

2026-11-26 source-resolution ledger:

- Added `benchmark_resolver.py`: explicit pinned GitHub artifact download, commit/expiry/size/upstream digest validation, safe one-wheel archive extraction, Docker builds of exact checked-out commits, bounded prefer-artifact strategy and retained source/wheel/builder provenance.
- Source preparation uses the existing durable supervised job slot and cancellation path. Stage progress is stored atomically. The UI offers Fetch, Build, or Reuse/build; completed wheels are reverified before populating candidate inspection and candidate SHA. Live model execution is still a separate action.
- Real controlled local Git + Docker build succeeded: source commit `f453544e50cb7c4d57e2232a8553dfa020a3d600`, builder ID `sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50`, receipt `.agentops/source-fixture-d283dvdk/resolved/source.json`.
- Real remote artifact preparation succeeded via supervised job `65c213efb572475790713e254d631a95`: headroomlabs-ai/headroom artifact `15092140066`, commit `4571390681b1763a390166d607a7fb7a1a735d24`. GitHub's upstream archive digest verified; produced `headroom_ai-0.45.0-cp310-abi3-manylinux_2_34_x86_64.whl`. No candidate code or provider request executed during that fetch.
- Independent review found ambiguity in incomplete prefer-artifact searches. Reproduced red/green; searches over 20 runs now require explicit source selection even when one visible wheel exists.
- Task 4 source materialization is implemented for GitHub artifacts and Docker builds; the main remaining work is tasks 2/3/5: measured designer/provider/model/feature capabilities, native two-build and tool adapters, durable grouped execution/recovery, and complete result exploration. The overall objective remains incomplete.
- Verification: focused backend regression set passed 63 tests; all 49 browser tests passed. The browser run exposed a file-picker close-event focus race; reproduced with a deterministic failing test, then removed redundant delayed focus restoration and verified native dialog focus behavior. Source-resolution independent review cleared its reported issue. Normal fixture build container cleanup verified with no remaining `agentops-build-*` containers.

2026-11-26 measured integration checkpoint (verification in progress):
- Added measured source/design/preflight/result wizard, bounded scenario/provider/model/feature/tool matrices, explicit model discovery and managed tool setup history. Native single-build and strict two-build executions use immutable grouped inputs, hashed evidence, durable progress, verified retry reuse and remote receipt collection before replay.
- Added client request identity reconciliation for lost submission replies; real offline fixture browser exercise verifies one job only. Fixture runs remain explicitly simulator-qualified.
- Genuine Inspect AI and AIPerf integrations each passed native grouped execution with actual pinned harnesses (2 tests, 207.81s); complete OpenAI fixture response corrected SDK validation. No live provider requests used.
- Review publication silence fixes are committed; live imports await the authorized safe relaunch. Reload helper/startup handoff preserve exact saved plans and queued jobs; independent review found additional rollback ownership edges under correction before activation.
- Remaining required scope includes distribution charts, cross-experiment comparison, broader saved-design library and throughput sweep support; this checkpoint does not complete the goal.
- Verification: full Python regression 670 passed, 18 skipped, 71 subtests; full browser regression 51 passed after focused help-overlay correction. Subsequent provider binding/provenance and transport/recovery checks: 45 passed. Reload ownership fixes: 56 passed plus 2 subtests. Independent measured review found provider routing issue; fixed explicit direct-protocol binding with eight regression cases.

2026-11-26 results exploration slice:
- Added per-arm input-token and latency sample dotplots, min/quartiles/median/max and sample counts with accessible tables. Missing measurements stay absent; small samples explicitly request more evidence. Added model filtering.
- Added saved-run comparison keyed by scenario/provider/model/tool/feature with source identities, lane labels, descriptive candidate token/latency changes, configuration-difference notices and guarded asynchronous loading. No new model requests are made by comparison.
- Verification: all 52 browser tests passed; independent results review cleared and independently reran eight provider-routing regressions. Mobile chart/table screenshots inspected; chart font enlarged and visible metric labels added, then focused comparison test passed.
- Runtime c5f0e4d relaunched from immutable detached checkout with saved configs and watch settings preserved; freshness reconciliation audit is still in progress. Overall goal remains active.
