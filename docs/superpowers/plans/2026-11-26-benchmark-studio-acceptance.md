# Benchmark Studio acceptance evidence

The approved four-stage Studio implementation is available on `feature/visual-benchmark-studio`. Deployment uses clean detached runtime checkouts so edits cannot change running benchmark identities. This document distinguishes executed evidence from prerequisites unavailable on this machine.

## Catalogue and first-run journey

Bundled simulator templates execute the actual paired runner, evaluation and hashed-evidence pipeline. They need no credentials and remain inconclusive for live qualification. Measured templates cover regression, context/compression, exact-answer quality retention, provider/model comparison, two builds and latency/throughput sweeps. The wizard exposes task prompts, expected answers, source selection, adjacent validation, request estimates and explicit preflight.

Evidence: `tests/test_benchmark_studio.py`, `tests/test_benchmark_experiments.py`, `tests/ui/benchmark-studio.spec.cjs` and `tests/ui/benchmark-measured.spec.cjs`. Actual live browser simulator demonstrations completed after each runtime handoff, including jobs recorded in the deployment receipts below.

## Sources and configuration

Repository-scoped commit/PR searches support SHA prefixes, messages, authors and PR selectors with bounded cached queries and stale-response protection. Source preparation explicitly fetches verified GitHub Actions wheels or builds pinned commits in Docker. Prepared and local source modes feed both native single-build and strict two-build execution.

An actual GitHub wheel was downloaded for `headroomlabs-ai/headroom`, artifact `15092140066`, commit `4571390681b1763a390166d607a7fb7a1a735d24`; its upstream archive digest was verified. A controlled pinned Git/Docker build also completed. Details and receipts are retained in the implementation ledger.

Ruling: visual feature overrides expose the supported cache/compression boolean declarations from the selected wheel's argparse configuration schema. Types, accepted boolean values, statically known defaults and help text are read without executing wheel code. Unknown declarations stay unavailable; arbitrary CLI tuning remains an explicit advanced runner configuration. This bounds supported feature combinations rather than inventing controls for unknown versions.

Evidence: source/resolver tests, nonexecuting selected-wheel schema test, source-selector browser tests and actual source receipts in the implementation ledger.

## Measured execution and durable evidence

Groups pin source digests, exact tasksets and revisions/digests, policy digest, selected configuration declarations/arguments, provider/model targets, tool versions and stable matrix identities. Execution consumes the pinned task and feature contents. Terminal cells retain strict native manifests with their evidence so exports include reproduction inputs. Legacy unpinned groups can be read or collected but cannot silently execute new requests.

Lost submission replies reconcile a persisted client request identity. Failed or uncertain remote work is collected before replacement execution. Explicit retries reuse verified completed cells. Completed groups reopen without replay. Cancellation and partial results remain distinct from passing qualification.

Actual pinned Inspect AI and AIPerf executions passed against controlled native fixtures. AIPerf load tests exercised concurrency 2 and 4 with exact upstream request budgets, isolated credentials and owned descendant cleanup. The native load-group integration produced ten verified observations and two measured arm-throughput summaries. Failed batches are reserved before invocation and never implicitly replayed by later paired observations. The complete matrix, including every task and concurrency level, is capped at 24 cells and 1,200 planned requests.

Actual Docker measured-group evidence: main workspace `.agentops/verification/docker-measured-59347f4-20261126T085241Z/docker-measured-audit.json`. Ten paired requests, verified identities/hashes, no replay on reopen, and both owned containers cleaned up. Its simulator decision remains inconclusive; this does not claim provider or general model-quality performance.

## Results and preservation

Results provide paired token bars, task success/threshold tables, individual token and latency distributions, quantiles, sample counts, uncertainty, per-cell evidence, filters including model/concurrency, descriptive saved-run comparison, duplication, named design snapshots and exports. Load results show measured requests per second with their measurement window and accessible numeric tables. Comparisons never promote descriptive differences into a new qualification decision.

Reviewers no longer publish saved/reused/unchanged-review notices without substantive additions. The reviewer changes and Studio updates were relaunched at verified checkpoints. Handoffs preserve queued review rows/configurations and restore watch settings; normal freshness reconciliation remains responsible for upstream changes. No review jobs are marked stopped or cancelled for deployment.

Main workspace deployment receipts: `.agentops/runtime-reload-c5f0e4d-verification.json`, `.agentops/runtime-reload-59347f4-verification.json`, and `.agentops/runtime-reload-ad46c56-verification.json`. Each records exact immutable assets, preserved queue/configuration identities, restored watches, authentication checks and an actual browser offline demonstration. Final throughput checkpoint deployment is recorded in the implementation ledger when complete.

## Verification boundaries

No OpenAI, Anthropic, Azure or AWS provider credentials are configured in the parent environment. No billed provider requests were made, and no live provider-performance claim is made. A live SSH host was unavailable; SSH transport/setup/recovery have automated tests, not a claimed live-host execution. Tools use actual pinned harnesses, and Docker was executed, rather than being represented by dataset exports or mocks.

The implementation ledger records final regression counts, independent review findings/fixes and the final deployment receipt. These boundaries are environmental prerequisites for users selecting live providers or SSH, rather than silent substitutions by the application.

Final code checkpoint: 67f20fe. Live verification preserved 49 queued rows and eight saved configurations, restored original watches and authentication, checked exact assets and all new controls, and completed offline demo f56f8c7b42b44412bc5beb2fb19a921e. Final receipt: main workspace .agentops/runtime-reload-67f20fe-verification.json. Final affected real-run tests: 49 passed; final browser regression: 55 passed. No implementation tasks remain.
