# Benchmark dashboard

User expected a Benchmarking tab after local deployment. Standing autopilot
approval applies to design, implementation, main integration and local restart.

Add a third top-level view matching existing design tokens and navigation.
Provide explicit steps to inspect a candidate wheel, bind inspection to taskset/
policy/model to create an experiment, and run local/Docker/SSH experiments.
Expose persistent run status, paired metrics, evidence paths and optional OSS
dataset import/export. No model or provider work starts by viewing the tab.

Backend is a dedicated benchmark job service using the existing CLI through
argument lists, never shell evaluation. Same-origin launch-token protection
applies to mutation routes. Work and logs stay below state/benchmarks. A
detached supervisor persists terminal status across panel restart, serializes
runs, and permits cancellation/collection without losing receipts. Return only
allowlisted numeric/status/path fields, never prompts, credentials or logs.

Navigation is keyboard accessible, persists selected view and integrates refresh.
Forms have labels, pending/disabled states, helpful validation and explicit
provider-call descriptions. Results distinguish pass/fail/inconclusive and
simulator evidence. The smoke dataset is not a release-quality benchmark.

1. Backend service + meaningful lifecycle and safety tests.
2. Frontend view + real browser interaction, error, persistence and mobile tests.
3. Wire routes/assets/navigation, verify packaging and regression suites.
4. Independent review, PR with passing checks, merge main and restart local
   panel using preserved configuration. Verify the actual Benchmarking tab.

User correction: every filesystem path needs a picker, not freeform entry.
Implement same-origin-token-protected read-only directory listing with file-type
filters and accessible Browse dialogs. User explicitly reaffirmed all-green
main integration and local restart on 2026-11-25 at12:24 CST.

## Verification before PR

- Root unittest: 413 tests passed, 6 optional skips (53.231s).
- Benchmark-related suite: 119 passed, 6 optional skips (65.80s).
- Browser interaction suite: 37 passed (46.3s), including seven new dashboard/
  picker tests. Existing priority geometry assertion now reads both rows in one
  browser task to avoid detachment during periodic refresh.
- Real offline candidate inspection, experiment creation and paired run tested;
  simulator evidence remains inconclusive and reports five pairs.
- Ruff check/format on new Python modules/tests and changed benchmark CLI;
  JS syntax, Actionlint, uv lock, build/Twine and high-severity Bandit passed.
- Wheel/sdist include dashboard service, file picker and JS/CSS; private state,
  validation logs and virtual environments excluded.
- Independent read-only review cleared all P1 findings: canonical artifact SHA,
  OS-level concurrent-slot protection, variable-name-only Docker credential
  forwarding, inspection collection and hashed/terminal decision gates.
- Supervisor-loss cancellation explicitly refuses unverified process signalling
  and retains the occupied slot; evidence never gains qualification from it.
- No paid provider calls or model runs performed; browser preview used isolated
  synthetic state. Main integration and live restart follow green remote CI.
