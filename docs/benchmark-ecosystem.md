# OSS benchmark interoperability

Reviewed against upstream documentation and published packages on 2026-11-25.
There is no single interchangeable LLM benchmark result format. Use established
harnesses for their datasets, scorers and load generation; retain the native
paired runner for Headroom-specific provenance and release decisions.

## Tested integrations

**Inspect AI 0.3.292:** native evaluation task, Sample JSONL import/export, and
its existing model-provider/log-viewer ecosystem. `inspect_task(taskset)` in
`agentops.benchmarks.interop` builds a real Inspect `Task`, applies each sample's
token budget and uses `includes(ignore_case=False)` to match our literal
substring grader. CI runs the actual SDK with `mockllm`, checks grading/token
budgets, and reads exported JSONL with Inspect's dataset reader.

```sh
python -m pip install -e '.[benchmark-test]' -r benchmarks/interop-requirements.txt
headroom-benchmark export-dataset benchmarks/tasksets/protocol-smoke-v1.json --format inspect --output .agentops/bench/inspect.jsonl
headroom-benchmark import-inspect .agentops/bench/inspect.jsonl --taskset-id imported-smoke --revision reviewed-v1 --output .agentops/bench/imported.json
inspect eval benchmarks/inspect_task.py -T taskset=tasksets/protocol-smoke-v1.json --model mockllm/model --log-dir .agentops/bench/inspect-logs
```

Inspect resolves relative taskset arguments from the task file directory
(`benchmarks/` here); an absolute path also works. The mock command tests plumbing; its default answer does not pass the smoke
tasks. For a dedicated Headroom endpoint use Inspect's `openai-api/NAME/MODEL`
provider and `NAME_BASE_URL` / `NAME_API_KEY` environment variables (for example
`headroom`, `HEADROOM_BASE_URL`, `HEADROOM_API_KEY`). See the
[provider documentation](https://inspect.aisi.org.uk/providers.html).
Pin the same candidate, routing, model and task revision for both arms. Inspect
owns streaming, retries and logs here; this lane does not test native stream
assertions or produce release qualification. Inspect logs contain transcripts.

Import accepts explicit unique string/integer IDs, string/text-chat inputs and
one nonempty literal target. Multiple choice, alternate targets, custom
metadata/scorers, setup/files/sandboxes, tools and multimodal content require an
explicit adapter and are rejected. Narrative targets are literal substrings
here: review the intended scorer before importing a model-graded dataset.
`metadata.headroom` preserves optional `max_tokens` and `stream` for roundtrips;
generic readers do not apply them. Our task factory applies token budgets.
Taskset identity/revision are explicit; archive source and converted hashes.

**NVIDIA AIPerf 0.14.0:** `single_turn` JSONL workload export. CI loads exports
with the real AIPerf loader and verifies prompt and output budget preservation.
AIPerf succeeds GenAI-Perf; prefer it over adding a legacy adapter.

```sh
headroom-benchmark export-dataset benchmarks/tasksets/protocol-smoke-v1.json --format aiperf --output .agentops/bench/aiperf.jsonl
aiperf profile --model PINNED_MODEL --endpoint-type chat --url http://127.0.0.1:8787 --input-file .agentops/bench/aiperf.jsonl --custom-dataset-type single_turn --concurrency 1 --request-count 2 --streaming
```

Use dedicated candidate proxies, once per arm, with identical inputs, client,
tokenizer, counts and concurrency. A1 adds `--no-optimize` to
`python -m headroom.proxy.server`; B uses the same candidate/command without that
flag. Set the backend explicitly in the proxy environment. External harness runs
can issue paid requests; export is offline. AIPerf export accepts one user text
message per task, rejecting system/multi-message context rather than flattening
it. `output_length` preserves budgets; streaming is a harness setting. Targets
are omitted because this workload measures serving performance. Archive its
native TTFT, inter-token latency, throughput and percentile reports separately.
Output tokens/client estimates are not proxy-measured optimization savings.

Sources: [Inspect datasets](https://inspect.aisi.org.uk/datasets.html),
[scorers](https://inspect.aisi.org.uk/reference/inspect_ai.scorer.html),
[AIPerf datasets](https://docs.nvidia.com/aiperf/v0.14.0/tutorials/datasets-inputs/custom-dataset-guide),
[GenAI-Perf comparison](https://github.com/ai-dynamo/aiperf/blob/main/docs/genai-perf-feature-comparison.md).

## Existing endpoint interoperability

**EleutherAI lm-evaluation-harness:** established task suites can connect through
`local-chat-completions` to Headroom's OpenAI-compatible chat endpoint. Use
generation-based tasks supported by that backend; chat cannot provide the log
probabilities many multiple-choice/perplexity tasks require. Preserve native
templates, dataset revisions, few-shot settings, tokenizers and scorers. No
lm-eval SDK/full-suite execution is tested here.
[Supported APIs/tasks](https://github.com/EleutherAI/lm-evaluation-harness#model-apis-and-inference-servers).

**vLLM GuideLLM:** another serving-load companion for concurrency, SLO/goodput
and trace replay. Version 0.8.1 uses `guidellm run` with
`--backend kind=openai_http,target=...`; older `guidellm benchmark` recipes are
stale. Use its native dataset adapters/reports; no custom parser or execution
validation is claimed here.
[Current guide](https://vllm-project.github.io/guidellm/stable/getting-started/benchmark/).

## Emerging standards and formal programs

**MLPerf Endpoints** is directly relevant to generative HTTP endpoints: its
reference client measures throughput/per-user-speed frontiers with quality
gates. For a standards-directed run, point that client at a dedicated Headroom
endpoint and preserve its supported model/dataset, tokenizer, measurement
points, disclosure files, checker and submission process. Proxy optimization
can affect model-equivalence/compliance; our A/B policy cannot establish that.
Pin the reference tools/rules at execution time. This is a documented endpoint
compatibility path, not a tested submission adapter or compliant result.
[Overview](https://docs.mlcommons.org/endpoints-submission-docs/understand/what-is-mlperf-endpoints/),
[runner CLI](https://docs.mlcommons.org/endpoints-submission-docs/reference/cli-inference-endpoint/),
[requirements](https://docs.mlcommons.org/endpoints-submission-docs/rules/requirements/).

**MLPerf Inference** prescribes model/system workloads and LoadGen/accuracy
rules. **AILuminate** evaluates safety/security using versioned taxonomies and
evaluators. Neither is replaced by protocol smoke tests. Agent suites such as
SWE-bench or Terminal-Bench require their own environments/graders; importing
their names or aggregate scores would not constitute integration.
[MLPerf Inference](https://github.com/mlcommons/inference),
[AILuminate](https://mlcommons.org/benchmarks/ailuminate/).

## Evidence and maintenance

Exports contain task inputs/targets: keep them and external logs private.
Import/export refuse to overwrite existing files. These commands accept
tasksets/Samples, not external scores/results. Release references still require
native hashed evidence, observed identities, complete pairs and terminal proof.

`benchmarks/interop-requirements.txt` pins the tested packages in a separate
Python 3.12 CI lane; neither becomes a runtime dependency. Update pins with
release notes and SDK/loader checks. Add adapters only with upstream fixtures
and explicit grading/provenance semantics. Cloud runs, full load tests, formal
submissions and paid evaluations remain separate operator runs; local tests
use mock models and local files.
