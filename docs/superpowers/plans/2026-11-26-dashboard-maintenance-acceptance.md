# Dashboard maintenance closeout

The implementation and integrated Visual Benchmark Studio landed in PR #33:
https://github.com/JerrettDavis/HeadroomAgentOps/pull/33
The merge revision is `41a7477617a01d484bf2f0242105e07c5217777a`.

## Verification

All 16 checks on the final PR head passed, including Windows/Linux Python
3.12–3.14, browser/package checks, interoperability/transports, audit and CodeQL.
The implementation run recorded 747 Python tests passing, 22 skipped and 71
subtests, plus 59 browser tests passing. Actual Docker harness and central
candidate validation also passed. Production PR rebases, workflow approvals and
paid-provider repairs were not triggered as acceptance tests.

At closeout, all 59 browser tests passed again against local main. The first
Python rerun used the root virtual environment, which lacks pytest and produced
27 import errors; it is not a passing validation run. The corrected rerun uses
the existing maintenance validation environment against the main checkout and
passed: **749 tests, 22 skips and 71 subtests**. Logs are locally preserved as
`.agentops/closeout-pytest.log` and `.agentops/closeout-ui.log`.

## Runtime boundary

The dashboard at localhost:8765 remains on immutable Visual Benchmark Studio
checkpoint `67f20fe`. Automatic policy review rejected the prepared guarded
runtime reload with “blocked by policy”; deployment is not accepted as complete.
The existing service and its queues were left running.

The prepared immutable checkout is
`.worktrees/runtime-dashboard-maintenance-41a7477`, with bootstrap
`.agentops/start-runtime-41a7477.py`. Before any later activation, take a fresh
snapshot and recapture the guarded handoff because queues and settings may have
advanced. Use `Launcher.resume_saved_runs()` before automatic scheduling. Never
use `agentops stop` for this reload: it cancels queued/live work. Verify auth,
saved configurations, historical runs, queues, watches and served assets after
activation using the prepared deployment verifier in the maintenance worktree.
The prepared Python bootstrap/verifier predate the user's .NET direction;
future work on this deployment path should assess a scoped .NET replacement
under `AGENTS.md` before adding orchestration.

## Local Test Host configuration

Repeated Test Host prompts were associated with path-specific .NET testhost
firewall entries. A user-local default runsettings file now selects the stable
`C:\Program Files\dotnet\dotnet.exe` using `DotNetHostPath`. Diagnostic discovery
with ordinary `dotnet test` confirmed that executable was launched without
invalid-settings warnings. No firewall rules or UAC policy were changed.

The file and rollback backups are under `%LOCALAPPDATA%\AgentOps\testing`.
The user `RunSettingsFilePath` environment value and PowerShell profile supply
the default. Existing processes require a restart to inherit it; explicit
runsettings override the default and need their own host setting. Absence of
future prompts has not yet been observed across all runners.
