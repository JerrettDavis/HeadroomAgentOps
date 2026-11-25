# OSS benchmark interoperability

Autopilot design and plan, approved by standing user instruction.

Use Inspect AI Sample JSONL as a reusable text-quality dataset interchange,
with strict bidirectional conversion and explicit preservation of native controls.
Provide a native Inspect task factory using its SDK and case-sensitive includes
scoring, verified with mockllm (no provider calls). Export AIPerf single-turn
workloads only where chat context can be represented without silent flattening.
Reject unsupported tools, multimodal inputs and execution-bearing dataset fields.
External formats and scores cannot enter the release-evidence/reference path.

1. Write failing converter, CLI and SDK interoperability tests.
2. Implement dependency-free conversion and optional Inspect task factory.
3. Verify real Inspect SDK and real AIPerf dataset parser with pinned versions.
4. Add pinned optional interoperability CI and usage/source/limits documentation.
5. Run regression/lint/package checks, review and update draft PR25.

Research priorities: Inspect AI (quality), EleutherAI lm-eval (task ecosystem),
NVIDIA AIPerf and vLLM GuideLLM (serving), MLCommons (formal benchmark programs).
No claim of MLPerf/AILuminate compliance or universal industry standard.

## Verification

Implemented CLI Inspect Sample JSONL import/export, optional native Inspect task,
and AIPerf single_turn export. Documented lm-eval/GuideLLM endpoint compatibility
and emerging MLPerf Endpoints reference-client path; no formal compliance claim.

- Native benchmark suite: 103 passed, 6 optional skips.
- Combined pinned Inspect0.3.292/AIPerf0.14.0 environment: 22 passed, no skips.
- Documented Inspect CLI mock invocation completed successfully; relative taskset
  arguments resolve from the task file directory.
- Root unittest: 410 passed, 6 skipped (56.813s).
- Ruff check/format, Actionlint, uv lock, build and Twine passed.
- Wheel/sdist include interop; sdist includes entrypoint and pinned requirements;
  private directories excluded. Independent targeted review found no blockers.
- No cloud provisioning, paid provider calls, full serving-load measurement,
  lm-eval/GuideLLM execution or MLPerf submission performed.
