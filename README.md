# Headroom AgentOps

Script-driven PR review with mechanical inventory and a fresh agent session for each PR. A Python coordinator owns the queue, evidence, shared SQLite ledger and GitHub writes. Each agent gets its own disposable checkout in a Linux container. It reviews one PR and exits.

Read [validation scope](docs/public-validation.md) for checks and qualification limits, and [repository automation](docs/automation.md) for CI and release setup. No PR merge or auto-merge action exists.

## Setup

### Install a release

Install Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv tool install "https://github.com/JerrettDavis/HeadroomAgentOps/releases/download/v0.1.0/headroom_agentops-0.1.0-py3-none-any.whl"
headroom-review --help
```

The same commands work in PowerShell. `pipx install <wheel-URL>` is an alternative.
The [latest release](https://github.com/JerrettDavis/HeadroomAgentOps/releases/latest)
includes version-matched installation instructions, wheel/source packages, an
example configuration and SHA-256 checksums. No source checkout is needed.

For review workers, also install Git, GitHub CLI and Docker with Linux containers:

```sh
docker pull ghcr.io/jerrettdavis/headroomagentops/worker:v0.1.0
gh auth login
gh release download v0.1.0 --repo JerrettDavis/HeadroomAgentOps --pattern agentops.example.toml
```

Copy the downloaded example to `agentops.toml` in a working directory and set
`repo` to the repository you want reviewed. The release config already selects
the matching worker image. Configure model authentication as described below,
then run `headroom-review doctor` and `headroom-review run --limit 1 --no-publish`.
Use `headroom-review status --details` or `headroom-review panel` to inspect work.

The prebuilt worker image targets Linux amd64; other architectures can build it
from source. Linux coordinators must run as a non-root user with a non-root
primary group. The worker image is launched by the coordinator; it is not a
standalone server. The `latest` image alias follows stable releases, while
versioned image tags are recommended for reproducible operation.
Each release also includes `worker-image-linux-amd64.tar.gz`, which can be
downloaded directly and loaded with `docker load --input <archive>` for offline
or registry-independent setup. It loads the same versioned image tag as the config.

### Develop from source

Clone this repository and use the reviewer setup below. The remaining launcher
examples show source-checkout usage; installed users can replace
`.\headroom-review.ps1` or `./headroom-review.sh` with `headroom-review`.

### Web panel

Run the dashboard against the same state directory used by your coordinator:

```powershell
cd G:\git\HeadroomAgentOps
.venv\Scripts\python.exe -m agentops --state .agentops panel --port 8765
```

Open <http://127.0.0.1:8765>. Viewing the panel needs Python; Docker and model authentication are not needed to view saved work. It binds to loopback. Stop the server with Ctrl+C.

Choose **New review** to start an individual PR, a batch of PR numbers or GitHub
URLs, all open PRs, or a GitHub query such as `author:username label:bug`.
Queries are scoped to the configured repository; incomplete searches or searches
over 1,000 results must be narrowed. Preview the matching PRs, deselect any you
want to exclude, and choose **Start selected reviews**. Previews expire after
30 minutes; changed or closed PRs require another preview.

The wizard uses the repository, worker image, and authentication settings from
`--config` (default `agentops.toml`). Choose a harness, optional model, and 1–4
parallel workers. Publication starts off; enabling it permits the coordinator's
existing guarded review-feedback publication. Launching requires GitHub CLI
authentication, Docker, the worker image, and the selected harness's model
credentials, just like `run`. Each selected PR gets a fresh isolated session;
wizard launches bypass reuse of earlier review evidence.
The new run appears in the run selector. Coordinator errors are reported in the
panel; full launch logs are retained under `<state>/launches/<run-id>.log`.
Closing the browser or stopping the panel leaves launched coordinators running;
use `headroom-review --state <state> stop <run-id>` to stop dispatch.

Select a run to inspect its queue, captured CI/review/conflict indicators, PR details, agent sessions, attempt history, review evidence, and comment publication receipts. Search the queue, drill into a PR, and switch between Details, Diffs, Comments, Review evidence, and Sessions. Themes follow the system by default, with persistent light/dark overrides. The ledger refreshes every five seconds; pause Auto to hold the view.

GitHub statuses come from saved inventory and review packets, not live GitHub polling. Session heartbeats describe coordinator activity, not a fresh worker process probe. Diffs show saved PR patches, exported candidate edits, and captured uncommitted edits. Binary or omitted patches are identified. Opening an exported candidate diff may fetch its missing base revision from GitHub into a disposable bare repository; Git is required for that view. No candidate code is checked out or executed.

Usage totals include historical attempts in the selected run, without counting the current result twice. Codex, Claude, and Copilot metrics are normalized; Copilot AI credits remain separate from reported USD. Missing usage/cost stays unknown, and active metrics appear when the coordinator persists them. Reused evidence is counted separately. Cached/reasoning token categories can overlap input/output, so do not sum all displayed rows.

### Reviewer setup

Requires Python 3.12+, Git, GitHub CLI, Docker with Linux containers, and model authentication for the chosen harness. Each active worker is capped at 8 GB RAM, two CPUs and 512 processes. Concurrency is configurable from one to four.

```powershell
cd G:\git\HeadroomAgentOps
uv sync --frozen
docker build -f worker/Dockerfile -t headroom-agentops-worker:local .
Copy-Item agentops.example.toml agentops.toml
.\headroom-review.ps1 doctor
```

Use your existing `gh` login for the coordinator. Workers receive no GitHub publisher credential, Docker socket, user home, primary checkout or shared Git configuration. Worker model authentication is copied separately; inherited harness hooks and MCP configurations are not copied.

Codex uses `OPENAI_API_KEY` or `auth.json` from `auth_root` (default `CODEX_HOME`, otherwise `~/.codex`). Claude uses `ANTHROPIC_API_KEY` or `.credentials.json` from `auth_root` (default `~/.claude`). Select an explicit model in configuration when stable model selection matters. Omitted models use the installed harness default, which may change server-side.

Copilot can use your native local sign-in through an inference-only host relay:

```powershell
.\headroom-review.ps1 run --pr 4760 --harness copilot --copilot-native --model gpt-6.1-sol --copilot-account JerrettDavis --no-publish
```

The relay keeps native OAuth on the host and gives workers a random model capability. It exposes only stateless Responses inference for the selected model, with no GitHub routes, account conversations, provider file references or server-side tools. The installed native Copilot CLI and Node must be available; use `--copilot-cli-root` if package discovery fails. Configure credentials locally, never in packets or chat. Native inference consumes Copilot AI credits; a zero premium-request count does not mean zero cost.

Copilot also supports BYOK through `COPILOT_PROVIDER_BASE_URL`, provider type, wire API, model IDs and a model-only API key or bearer token. Alternatively `AGENTOPS_COPILOT_TOKEN` must have inspected model-only OAuth scopes. A general repository-writing token is rejected for worker staging.

## Run

Start with a small review-only pilot:

```powershell
.\headroom-review.ps1 run --harness codex --concurrency 2 --limit 2 --no-publish
.\headroom-review.ps1 status --details
```

Review selected PRs with another harness:

```powershell
.\headroom-review.ps1 run --pr 4760 --harness claude --auth-root C:/Users/you/.claude --no-publish
```

Run the complete current queue with safe maintainer publication enabled:

```powershell
.\headroom-review.ps1 run --harness codex --concurrency 2 --publish
```

`./headroom-review.sh` exposes the same commands on POSIX systems. With no arguments, either launcher runs the full current queue using `agentops.toml`; publication defaults to disabled until explicitly configured. Models, harnesses, paths, budgets and concurrency are selectable without changing coordinator code.

To inspect the work before starting agents, persist a plan first:

```powershell
.\headroom-review.ps1 plan --pr 4780 --pr 4748
.\headroom-review.ps1 run --plan RUN_ID --no-publish
```

Planning paginates the open upstream queue, confirms membership/heads in a second pass and checks that main stayed fixed. It sorts updated chopratejas PRs first, new/Review Required PRs next, then newest remaining changes. Dispatch fetches complete PR discussion, reviews, nested threads, changed files, raw checks and actual workflow runs. Expected workflows are derived from exact-head YAML source and branch/path filters; unsupported trigger patterns remain explicit qualification gaps.

The initial packet is bounded to 24 KiB. Substantive discussion is preserved in a normalized evidence file; raw API responses remain available separately. Agents can retrieve details without receiving the entire historical ledger or queue in their conversation.

An unchanged revision can reuse hash-verified prior evidence with the same policy version, target base, discussion, workflow source and relevant notices. Changed author evidence requires fresh reasoning. Use `run --force-fresh` to bypass reuse. Imported snapshots must match the configured repository.

Unchanged questions and undelivered feedback remain recorded without repeating the model review. Archived fix intents retain their original ownership across reuse passes and still pass the publication guards. Pending CI progress is mechanical; a new terminal failure requires fresh reasoning. An execution request based on an older CI state stays held.

## Review and publication

Workers can prepare clean main integrations, mechanical conflict resolutions, small maintainer fixes, tests and documentation repairs. They must preserve contributor intent and leave semantic choices or missing proof as findings. The coordinator imports candidate Git bundles into a separate trusted bare repository before a leased fast-forward push. It never executes worker Git configuration on the host.

Comments, reviews, fixes and CI actions are structured intents. Before publication, the coordinator rechecks the live PR head, current upstream main and discussion evidence. Approval additionally requires complete file/thread coverage, passing local evidence, expected hosted workflow coverage, successful actual checks or a recorded exact baseline exception, confirmed mergeability and source provenance review. Owner PRs require independent human approval. Explicit lifecycle, live-proof and policy holds remain recorded.

Code pushes and CI execution require source review bound to the exact workflow manifest. Workflow/tooling changes in candidate bundles currently require additional provenance handling and are held. A new candidate head requires a fresh qualification; publishing a fix never approves it automatically. Readiness is evidence with stated limits, not a merge instruction.

Action IDs are stable across runs for the same PR/head/main/action. A write is journaled before it is sent. Lost comment/review responses are reconciled by markers; pushes by live head; CI reruns by run attempt. An unresolved ambiguous write remains held rather than being retried blindly.

## Shared state and recovery

State lives under `.agentops/` and is excluded from Git and Docker build contexts:

- `ledger.sqlite3`: snapshots, runs, claims, per-PR results, action intents and shared notices.
- `evidence/`: immutable, hash-checked JSON artifacts.
- `jobs/SESSION/input/`: assignment, compact packet and relevant shared notices.
- `jobs/SESSION/output/`: result, logs, measured usage, patches and candidate bundles.

The coordinator delivers relevant notices to live workers. Workers acknowledge notice IDs; newly applicable evidence that has not been reviewed requires follow-up. Worker suggestions retain provenance and cannot promote themselves to verified global policy or baseline exceptions.

```powershell
.\headroom-review.ps1 stop RUN_ID
.\headroom-review.ps1 reconcile
.\headroom-review.ps1 retry JOB_ID --reason "New CI result needs examination"
.\headroom-review.ps1 recover JOB_ID --publish
.\headroom-review.ps1 run --plan RUN_ID
```

Stop prevents new dispatch; an observing coordinator cancels its owned containers. Workers also have an intrinsic session time budget. Claims are not reclaimed merely because a timestamp is old. Recovery checks coordinator PID and creation identity plus actual labeled container state. Restarting a run can adopt a verified orphaned worker without starting a duplicate conversation. Missing or unobservable handles remain distinct.

`recover` requalifies a completed, valid worker result after a GitHub read failure without starting another model session. It checks the original hashed inputs, current revision/discussion/notices, and verified prior publication receipts. Arbitrary worker failures cannot use this path. Recovered historical stale records retain their stale flag and cannot authorize approval, code pushes or CI. Ordinary `retry` starts a bounded fresh reasoning session.

Default budget is 60 minutes per session and at most three reasoning sessions for one revision within a run. Budget exhaustion, missing authentication, malformed results and failed checks are visible outcomes. Held, failed, awaiting-CI and fully qualified work are reported separately. Main/head changes invalidate affected evidence; no old-main result becomes current simply because its checks are green.

Terminal job checkouts and staged authentication are removed only after verifying ownership and process termination. Evidence and candidate bundles remain. User files in the primary Headroom checkout are never used as worker scratch space.

Candidate bundles are also retained as content-addressed evidence before cleanup, allowing later guarded publication without the original container. Resuming a run first reconciles terminal CI and queues bounded follow-ups for new discussion, shared evidence or terminal failures. Pending progress alone does not start another agent. Baseline exception import and publication of protected workflow changes remain unavailable; these cases stay held.

## Verification

```powershell
uv run --frozen python -m unittest discover -s tests -v
git diff --check
```

The suite covers transactional claims, pagination failures, changed queue membership, evidence integrity, separate sessions, workflow applicability, publication races, duplicate feedback, candidate bundle import with an actual local leased push, source guards and cleanup. Qualification limits are recorded in [validation scope](docs/public-validation.md). Private operational records are not part of this distribution.

Use `worker/smoke.py` for a small real harness contract check. It prints a container/session handle; observe that exact handle until terminal before cleanup. Never infer that a model finished from a state file alone.
