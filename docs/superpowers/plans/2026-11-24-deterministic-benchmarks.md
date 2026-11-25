# Deterministic release benchmarks implementation plan

> Execute inline using superpowers:executing-plans. Plan automatically approved by user.

**Goal:** Run paired A1/B black-box candidate benchmarks locally, in Docker, and on remote hosts, with Azure and Bedrock provider support.

**Architecture:** A standalone `headroom-benchmark` CLI orchestrates immutable experiments. Transport (local/Docker/SSH) is independent of provider. Seeded schedules pair identical tasks across optimization states. Versioned policy evaluates paired observations; missing evidence fails closed. Raw evidence is content addressed and a release-contract reference identifies it.

**Tech stack:** Python 3.12, standard library, existing packaging; optional boto3 for Bedrock.

**Sources:** headroomlabs-ai/headroom upstream main 234c233cc, ADR 0003 and 0004, release/contracts/benchmark-result-ref.schema.json; PRs 3636–3639.

## Constraints

A1 and B share candidate SHA, verified artifact bytes, model, agent, taskset, rollout registry/snapshot, environment and limits. Only optimization differs. A0 never qualifies. Provider/infrastructure errors, cancellations, identity mismatches and absent telemetry produce inconclusive evidence. Seeds reproduce schedules, not live model outputs. No credentials in manifests or evidence. Existing review runtime remains untouched.

## Review focus

Unobserved identity cannot qualify; partial attempts remain visible; invalid numeric telemetry cannot pass; remote commands preserve argument boundaries; simulation cannot impersonate live qualification.

## Tasks

- [x] 1. `agentops/benchmarks/core.py`, `tests/test_benchmark_core.py`: canonical hashes, seeded paired schedule, strict manifest validation, policy-based paired evaluation and drift detection. Write failing tests, observe failure, implement, verify.
- [x] 2. `agentops/benchmarks/providers.py`, `runner.py`, corresponding tests: public protocol clients, verified artifact startup, provenance observation, attempts and terminal evidence. Cover failures, cancellation, and missing telemetry.
- [x] 3. `agentops/benchmarks/cli.py`, `transports.py`, `pyproject.toml`: local CLI, Docker/SSH execution, artifact transfer and result retrieval. Verify argument construction and end-to-end hermetic proxy experiment.
- [x] 4. `benchmarks/Dockerfile`, example manifests and tasks, `docs/benchmarks.md`: runnable local/Docker/remote recipes including droplets, Azure VMs, AWS hosts and Bedrock; credential references only.
- [x] 5. Validate emitted reference against pinned upstream contracts, run regression suite, independently review complete branch and correct findings. Record unavailable live infrastructure validation explicitly.
