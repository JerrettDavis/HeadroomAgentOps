# Contributing

Use Python 3.12 or newer. Install with `uv sync --frozen`, then run
`uv run --frozen python -m unittest discover -s tests -v`.

For real worker integration tests, build
`docker build -f worker/Dockerfile -t headroom-agentops-worker:local .`, set
`AGENTOPS_DOCKER_TESTS=1`, and run the tests again. These use deterministic
fake harnesses and require no model or GitHub credentials.

Explain the behavior change and verification in each PR. Preserve contributor
intent, credential isolation, evidence integrity and guarded publication.
Never include tokens, local state directories, private review packets or
model credentials. See [security](docs/security.md) and
[repository automation](docs/automation.md).

Maintainers should review changes to workflows, dependency locks, worker
tooling and publication policy before approving. Require independent review
for changes to authorization or credential boundaries. Automated checks do not
replace the exact-source provenance review required by the coordinator.
