# Paired release benchmarks

See [OSS interoperability](benchmark-ecosystem.md) for tested Inspect AI and
AIPerf integrations, companion harnesses and emerging MLPerf Endpoints guidance.

`headroom-benchmark` runs independently of the AgentOps review panel and workers.
It implements Headroom ADRs [0003](https://github.com/headroomlabs-ai/headroom/blob/234c233cc439aedba53b242f02f695de5f90c877/docs/adr/0003-a1-b-release-benchmark.md)
and [0004](https://github.com/headroomlabs-ai/headroom/blob/234c233cc439aedba53b242f02f695de5f90c877/docs/adr/0004-black-box-release-benchmarks.md):
A1 is the candidate proxy with optimization disabled, and B is the **same wheel**
with optimization enabled. Direct-provider A0 is not a qualifying comparator.

Install Python 3.12+ and run `python -m pip install .` from this repository.
All commands below also work as `python -m agentops.benchmarks.cli ...`.
Replace `CANDIDATE.whl` with the wheel's original filename, `SOURCE_SHA` with its
40-character source commit, and `sha256:DIGEST` with the digest from the immutable
candidate manifest. Verify the candidate manifest's provenance before using it.
The worker verifies wheel bytes before installation; it does not establish the
source-to-wheel relationship independently of the candidate manifest.

## Dashboard

Open the local dashboard and select **Benchmarking**. Viewing this tab only
reads saved status; it does not start provider requests or PR reviews.
Use Browse controls to select local files on the machine running AgentOps.
Pickers filter wheels, JSON configurations and JSON/JSONL datasets and provide
folder, local-drive and home navigation. They read directory entries, not file
contents. Network locations and filesystem links are not available in the picker.

1. Inspect a candidate wheel, choosing its runner configuration and local,
   Docker or SSH transport. For Docker, select environment variable names to
   forward credentials; never enter credential values in the dashboard.
   Inspection installs and starts the candidate.
2. Create an experiment from that inspection, pinned source/model identities,
   taskset and policy, seed and repeats.
3. Run the experiment using the same candidate and matching transport.
   Provider calls begin only after submitting the operation.

The dashboard retains jobs and evidence below the configured state directory's
`benchmarks` folder. A worker supervisor records completion independently of the
panel, so closing the browser or restarting the panel does not restart a run.
Saved status and result paths remain available when you return. Cancellation
and collection preserve the original run/transport receipts. Concurrent launches
are serialized independently of review workers.

Results show native pass, fail or inconclusive decisions and paired confidence
intervals when available. Simulator evidence remains explicitly inconclusive.
Inspect/AIPerf dataset exchange is available alongside the native workflow;
external scores do not become release evidence. Inputs, candidate-install logs
and dataset exports remain private. The bundled smoke taskset checks protocol
behavior and does not establish release quality.

## Local execution

Set the provider credentials in the worker's environment, then inspect the
candidate using its supported `/health` and `/stats` provenance surfaces:

```sh
headroom-benchmark inspect --artifact CANDIDATE.whl --sha256 sha256:DIGEST --config benchmarks/runner.openai.example.json --output .agentops/bench/inspection
headroom-benchmark init --inspection .agentops/bench/inspection/inspection.json --candidate-sha SOURCE_SHA --model PINNED_MODEL_VERSION --tasks benchmarks/tasksets/protocol-smoke-v1.json --policy benchmarks/policies/smoke-v1.json --experiment-id smoke-001 --seed 42 --repeats 5 --output .agentops/bench/experiment.json
headroom-benchmark plan .agentops/bench/experiment.json --output .agentops/bench/plan.json
headroom-benchmark run .agentops/bench/experiment.json --artifact CANDIDATE.whl --config benchmarks/runner.openai.example.json --output .agentops/bench/smoke-001
```

Inspection installs into a temporary venv and starts a proxy, without making a
model-generation request. `init` records observed rollout, agent, environment,
taskset revision/digest and approved policy inputs. The example performs 20
model requests: two tasks × five repeats × two arms. Use a pinned model version
whose response `model` field matches the manifest. If a deployment routes by an
alias, put that alias in runner configuration `request_model`, keeping the actual
version in the manifest.

The built-in HTTP agent replays fixed message histories, grades expected answer
substrings and optional tool-call names, and supports non-streamed and streamed
OpenAI/Azure or Anthropic/Bedrock responses. Tasks may contain `tools`,
`tool_choice`, `expected_tools`, `stream`, and `max_tokens`; tool schemas and
message histories must use the selected provider's protocol. Tool-call assertions
check requested names; this agent does not execute model-supplied tool commands.
Use representative, versioned tool-result histories for compression benchmarks.
The included smoke taskset checks protocol operation; it is not a representative
release savings workload. The release policy example is illustrative and must
be registered/reviewed with your qualification policy before release use.

## Docker execution

```sh
docker build -f benchmarks/Dockerfile -t headroom-benchmark:local .
headroom-benchmark inspect --artifact CANDIDATE.whl --sha256 sha256:DIGEST --config benchmarks/runner.openai.example.json --output .agentops/bench/docker-inspection --transport docker --image headroom-benchmark:local --cpus 2 --memory-gib 4 --env OPENAI_API_KEY
```

Use the Docker inspection for `init`, then add the **same** transport/image/limit
arguments to `run`. The transport resolves image tags to immutable image IDs,
enforces CPU and memory/swap limits, and reads effective cgroup limits inside the
worker. `--env NAME` forwards an existing variable by name; secret values never
appear in command arguments or result evidence. Image builds use a pinned Python
base; `--build-arg PYTHON_IMAGE=...@sha256:...` supports another approved base.

Docker runs have unique container names and a `docker-receipt.json` in the output
directory. Inputs and partial evidence remain in a private `.docker-UUID`
subdirectory, including if the Docker client loses its connection. Recover with
`headroom-benchmark collect OUTPUT/docker-receipt.json --output OUTPUT`; this
checks the same container and never launches another one. A live container or
unavailable daemon returns 75. Ctrl-C/SIGTERM requests a graceful stop of that
owned container with a 60-second grace period and preserves an inconclusive
result. Keep the receipt and staged files until collection is complete; remove
that specific `.docker-UUID` directory after archiving when no longer needed.
Collection verifies terminal status, exit code, OOM state and runtime errors,
binds that proof into the hashed result, and persists it in the receipt before
removing the stopped container by its observed ID. Cached proof permits repeat
collection after removal. Missing or conflicting terminal proof is inconclusive.
SSH likewise records the supervised worker subprocess exit code; observation
loss does not restart or cancel the worker.

Each inspection/run installs a fresh candidate. Pin its dependencies with an
offline wheelhouse: set `wheelhouse` in runner JSON to a directory containing the
candidate and its complete dependency wheels. The transport stages only `.whl`
files from that directory and uses `pip --no-index`. Online installs remain
useful locally, but any package fingerprint change between inspection and a run
makes its evidence inconclusive. Native execution records host CPU/RAM
availability; Docker provides enforced per-run limits. Prefer Docker for release
evidence on shared hosts.

## Droplets, Azure VMs and AWS hosts

Any Linux host with Python 3.12+, venv/pip and SSH can execute the same worker.
Use ordinary OpenSSH host aliases, identity files and verified host keys. The
transport does not provision or delete cloud infrastructure.

```sh
headroom-benchmark inspect --artifact CANDIDATE.whl --sha256 sha256:DIGEST --config benchmarks/runner.openai.example.json --output .agentops/bench/remote-inspection --transport ssh --host bench@droplet.example
headroom-benchmark run .agentops/bench/remote-experiment.json --artifact CANDIDATE.whl --config benchmarks/runner.openai.example.json --output .agentops/bench/remote-001 --transport ssh --host bench@droplet.example
headroom-benchmark collect .agentops/bench/remote-001/remote-receipt.json --output .agentops/bench/remote-001
```

Create `remote-experiment.json` with `init` using that host's inspection first.
Replace the host with your Azure VM, EC2 instance, or another SSH target;
`--ssh-port` and `--ssh-config` select a port/configuration. For enforced limits
on a remote host, install this package there and execute its Docker transport.
The SSH-native transport records the remote host's effective available limits.

Credentials remain on the remote machine. The worker optionally loads a private
`~/.config/headroom-benchmark/env.json` object of environment variable names to
string values; Unix permissions must be 0600. It also supports the host's usual
AWS credential chain/instance role. Do not put secret values in experiment JSON,
proxy argument lists, or tasksets. Inputs are uploaded to a unique `/tmp` run
directory. A local receipt records that directory before execution. Collection
downloads and verifies the same run's hashed evidence; it never starts or retries
a worker. Interrupted observation returns 75 while a checked remote PID is live
or SSH is unavailable, and 2 if a terminal/missing worker has no complete result.
Remote staging remains available for recovery; remove that specific receipt's
directory after collecting and archiving it.

On explicit controller cancellation the SSH transport signals only the recorded
worker PID after verifying its command identity, waits for graceful shutdown,
and collects its partial result as inconclusive. Lost observation alone does
not stop a worker or start a replacement.

Provider configuration is independent of transport:

* **OpenAI:** `runner.openai.example.json`, with `OPENAI_API_KEY`.
* **Azure OpenAI:** `runner.azure.example.json`, with `AZURE_OPENAI_API_KEY` and
  `OPENAI_TARGET_API_URL` set to the Azure v1-compatible base
  `https://RESOURCE.openai.azure.com/openai` (the proxy appends `/v1/...`).
  Set `request_model` to a deployment name when it differs from the returned
  pinned model version. A v1-compatible gateway can also provide Azure routing.
* **AWS Bedrock:** `runner.bedrock.example.json` installs `[proxy,bedrock]` and
  starts the native Bedrock backend. Supply the actual Bedrock model ID plus
  region and IAM role/profile/SDK credentials on the worker. Traffic uses the
  candidate's `/v1/messages` endpoint, including AWS signing inside Headroom.
* **Anthropic:** use protocol `anthropic`, `api_key_env: ANTHROPIC_API_KEY`, and
  the candidate's `/v1/messages` endpoint.

Forward additional provider/rollout variables to Docker explicitly with `--env`.
Bedrock and Azure protocol adapters have hermetic coverage; live cloud/model
performance requires your configured provider resources and credentials.

## Decisions and release evidence

A seed fixes task pairing, repeat identities, and randomized arm order. Python's
runtime hash seed is the experiment seed modulo 2³². It does
not make live providers, network timing or model outputs deterministic. Each
attempt starts a fresh proxy/cache/config home. The agent implementation digest,
wheel digest, source SHA, model, rollout digests, taskset, environment and limits
must match the manifest and both arms. Runtime state is observed before and
after every request. The source SHA is candidate-manifest attested; producer
metadata supplies the execution trust boundary.

Policy version 1 explicitly selects `paired_t_95`: 95% paired Student t intervals
for input-token savings, accuracy regression, and latency ratio. The upper/lower
bounds must satisfy every threshold, both arms must meet `minimum_accuracy`, and
the policy's `missing_data_status` must be `inconclusive`. This assumes independent paired samples;
repeated tasks and correlated provider load need sufficient diverse tasks and a
policy selected for that workload. Constant samples have zero estimated variance;
small/constant runs are not a universal model-quality guarantee. Noisy, complete
runs can fail policy. Provider failures, missing usage/model/provenance, unsafe
rollout overrides, truncated streams, identity drift, duplicate/missing attempts,
invalid telemetry and graceful cancellations are inconclusive. Exit codes are
0 pass, 1 fail, 2 inconclusive, 64 invalid input/execution setup, and 75 recoverable
remote observation/collection. SIGKILL and machine loss cannot flush local
evidence; remote receipts permit later collection where the worker survived.

Results contain sanitized attempt telemetry and deterministic grading outcomes,
not prompts, model responses, credentials or proxy logs. Private retained
transport staging includes task inputs and must not be published with results. The raw JSON filename
and `summary.json` identify its canonical SHA-256. Raw files cannot be overwritten
with different content. Keep one output directory per experiment invocation.
The raw result also records a digest of the runner configuration excluding local
wheelhouse/diagnostic paths. Archive that credential-free configuration alongside
the experiment so provider routing and proxy arguments can be reproduced.
Optional `diagnostics_dir` in runner JSON retains private proxy logs for startup
debugging; they may contain operational/provider data and are never collected or
published as release evidence.

The manual `Release benchmark` workflow accepts a committed immutable experiment,
candidate-wheel URL and worker image pinned by registry digest. It exposes only
provider credentials to Docker, retains raw results even on failed/inconclusive
experiments, exports the upstream release reference, and attests artifacts using
GitHub OIDC. Configure the repository's `release-benchmarks` environment and
provider secrets/variables before dispatching. It does not receive package
publishing credentials or mutate the Headroom repository.

`reference MANIFEST --result RAW.json --uri HTTPS_URI --output REFERENCE.json`
is available in that GitHub Actions publication context. It rechecks pairing,
manifest/taskset binding, observed identities and policy decisions before export.
Pinned schemas and upstream source/license are packaged under
`agentops/benchmarks/contracts`. The reference's producer fields alone are not
authentication: the qualification controller must authenticate the artifact
attestation against the expected repository/workflow/commit/run, retrieve the raw
file from the referenced Actions artifact, verify its SHA-256, apply the
registered policy, and reject missing/revoked/expired evidence. Artifact retention
defaults to 90 days; archive immutable raw evidence and attestations before then
when your release policy requires longer retention.

`--lane simulator` always produces inconclusive qualification evidence, even when
the simulated comparison passes. The bundled test-wheel identity also forces
that lane automatically. Hermetic fixtures prove infrastructure/protocol behavior;
live evidence must use the registered candidate and real provider path.

## Development validation

```sh
python -m pip install -e '.[benchmark-test]'
python -m pytest tests -q -k benchmark
docker build -f benchmarks/Dockerfile -t headroom-benchmark:validation .
docker build -f tests/fixtures/benchmark-ssh.Dockerfile -t headroom-benchmark-ssh:validation .
```

Set `HEADROOM_BENCH_DOCKER_IMAGE=headroom-benchmark:validation` and
`HEADROOM_BENCH_SSH_IMAGE=headroom-benchmark-ssh:validation`, then run
`python -m pytest tests/test_benchmark_docker.py tests/test_benchmark_ssh.py -q`.
The SSH test uses an ephemeral key and a pinned host key on a disposable Linux
container. It does not use cloud credentials. The SSH fixture image is only for
testing. Set `HEADROOM_BENCH_CANDIDATE_WHEEL` to an actual compatible Headroom wheel
to run the optional real-proxy/local-provider wire test; it remains simulator
evidence. CI runs native tests across supported Python/OS versions and both real
transport tests on Linux.
