# Visual Benchmark Studio

## Intent and current evidence

Make benchmarking usable from a fresh installation without requiring knowledge of wheel files, JSON tasksets, policies, or manifests. Preserve reproducibility and distinguish demonstrations from measured provider performance.

The current main branch has inspection, experiment creation, execution, dataset import/export, job history, runner selection, contextual help, and a tutorial. `agentops/ui/benchmarking.js` still requires artifact paths, full SHAs, model text, taskset files, and policy files. `BenchmarkService._argv` requires existing local files for inspect/run. No source resolver, commit search, template catalogue, or visual experiment matrix exists. The tutorial explains prerequisites rather than eliminating them.

## User journey

The Benchmarking landing page presents Start a demo, Design an experiment, and Open saved experiment. Show recent experiments with outcomes, source provenance, and clear next actions. A first run can execute an explicitly labeled deterministic simulator demo without credentials or charges; it must exercise the actual job and result pipeline. A separate measured quickstart checks runner dependencies and credentials and explains expected requests before launch.

Use an embedded, persistent, non-modal wizard with Back/Next, saved drafts, validation beside fields, keyboard navigation, and a review step. Users can revisit any step without losing unrelated selections. Advanced controls stay available through progressive disclosure.

## Sources and intelligent selectors

Baseline and candidate each offer local artifact, repository commit, and remote build artifact. Repository commits are searched by SHA prefix, message, author, and PR number/title. Results show abbreviated SHA, subject, author/date, PR association, and available build status. Resolve selections to immutable full SHAs. Never silently substitute branch HEAD or a different artifact.

Use bounded server-side search with pagination, repository-scoped cache and TTL, indexed local commit metadata, debounced client requests, cancellation, and stale-response protection. Do not clone/fetch the repository or enumerate all workflow artifacts on every keystroke. Search states distinguish no matches, authentication failure, unavailable remote, and loading. Keyboard-accessible comboboxes expose selection and result count.

Remote artifact cards identify repository, workflow/run, commit, artifact name, size, creation/expiry, and runner compatibility. Prefer available artifacts for the selected immutable commit; otherwise offer an explicit build action with visible command, prerequisites, and log/progress. Download into a managed cache, verify digests and archive paths, and persist provenance. Expired artifacts have an actionable alternative. Building repository code is an explicit user action in the selected isolated runner; discovery never executes it.

## Experiment designer

Provide selectable tool, feature, scenario, provider, model, and runner cards, with descriptions, prerequisites, supported combinations, and availability. Populate capabilities from backend catalogues rather than static UI guesses. Implement supported adapters before advertising them as runnable; explain unavailable integrations inline.

Tool selection supports one or several tools. Scenario selection includes task previews and expected evidence. Feature selection exposes applicable candidate configuration and links each feature to measurements that can evaluate it. Provider/model selectors use configured capabilities, permit explicit custom model identifiers, and never display credentials. Runner settings include local, Docker, and SSH with connection/preflight feedback.

Show a visual matrix of selected tools × scenarios × providers/models × candidate variants. Explain repetitions, seed, concurrency, pairing, request estimate, and limits. Enforce a bounded expansion, and allow inspection of every planned run before submission. Each cell retains the complete immutable configuration. Never combine incompatible tool metrics into a single unexplained score.

## Templates and ready-to-run examples

Ship versioned bundled templates with descriptions, tasksets, policies, default parameters, compatible tools/features/providers, and prerequisite metadata. Initial catalogue: credential-free simulator introduction, paired candidate regression, latency/throughput sweep, compression effectiveness, quality retention, and provider/model comparison. All templates can be previewed and edited. Measured templates require real prerequisites; demo templates remain visibly marked in cards, running views, charts, and exported evidence.

## Execution and visual exploration

Review shows resolved source provenance, full matrix, checks, thresholds, expected request count, provider-charge notice where applicable, and exact run settings. Submission creates a durable experiment group and bounded child jobs. Support cancel, partial failure, retry of failed cells, and reload/recovery without duplicate work.

An experiment workspace shows live stage progress and per-cell status. Results offer paired baseline/candidate bars, distributions where samples support them, absolute and percentage changes, sample count, uncertainty, thresholds, and per-scenario breakdown. Chart values have an accessible table. Users filter and compare runs, inspect tasks and failures, duplicate/edit experiments, and export reproducible manifests and evidence. A completed process and a passing experiment remain separate states.

## Implementation choices

Build on the existing Python service and plain JavaScript/CSS panel; do not introduce a second application framework. Add source resolution, catalogue/preflight, draft/group persistence, and result aggregation as separate backend modules. Keep the current CLI and single-job API compatible. Use managed directories for downloads/build outputs and existing runner isolation. Credentials remain in configured runner environments.

## Delivery and proof

Builder review clarified these defaults:

- Baseline mode is explicit: passthrough versus one Headroom candidate (existing semantics), or two pinned Headroom builds. The latter requires a versioned two-build runner contract; do not present source selection as implemented until both builds actually execute.
- Initial remote service is GitHub Actions artifacts. Repository configuration supplies allowed workflow/artifact patterns and an explicit Python wheel build recipe. Keep the source interface extensible for other remote artifact services.
- Initial executable tools are the native paired runner, Inspect AI, and AIPerf. Add actual execution adapters and tool-specific evidence readers; dataset export is not execution. Other ecosystem tools remain discoverable with honest prerequisite/status descriptions until their adapters exist.
- Derive feature controls from the selected Headroom version's validated configuration schema, with labels, descriptions, valid values, and scenario compatibility. Unknown capabilities require inspection rather than invented toggles.
- A versioned group manifest pins template/taskset/policy versions and digests, resolved features, tool versions, provider/model identities, both source digests, and stable cell identities. Group storage wraps compatible single-job manifests rather than adding unsupported fields to their strict schema.

1. Catalogue, bundled tasks/policies, simulator template, and real one-click demo with accessible result charts.
2. Persistent visual designer, editable templates, capability-aware fields, preflight, and review.
3. Cached commit/PR search, remote artifact resolution, explicit builds, provenance and source cards.
4. Matrix execution groups, recovery/cancellation, measured integrations, and cross-run exploration.

Completion requires all four stages. Verify fresh-install demo end to end; reload a draft and active group; use keyboard-only wizard and source selectors; resolve partial SHA/message/author/PR searches against fixture and real repository data; measure bounded search behavior and stale-response handling; download and build selected immutable sources; run supported measured integrations; verify matrix completeness, cancellation and partial failure; and inspect rendered desktop/mobile charts against underlying evidence. Existing regression and UI checks must pass. Live provider execution requires available configured credentials and must not be claimed from simulator or stub results.

These are proposed implementation choices derived from the requested outcome, not additional user requirements.

## Current implementation anchors and acceptance gaps

- `agentops/panel.py`: existing benchmark submit, cancel, and status routes can host compatible additions. Catalogue, source search, drafts, and experiment-group routes are missing.
- `agentops/benchmark_service.py`: existing supervised jobs and sanitized summaries are reusable. Source resolution, group orchestration, and tool adapters remain to be implemented.
- `agentops/ui/benchmarking.js` and `benchmarking.css`: current forms and tutorial are the migration surface; retain access to existing jobs while adding the visual journey.
- `tests/test_benchmark_dashboard.py::test_real_offline_inspect_create_run_preserves_simulator_boundary` already proves an offline inspection/create/run path. It is the starting point for a bundled demo, not proof of a fresh-user one-click experience.
- Existing core tests cover reproducible scheduling, paired observations, invalid evidence, and pinned identities. Extend those contracts for two-build execution and grouped manifests rather than treating the current single-candidate checks as sufficient.
- New browser coverage must execute the actual demo through panel routes, inspect resulting evidence, reload drafts/groups, and cover keyboard navigation. A mocked form submission alone does not establish runnable templates or result correctness.
