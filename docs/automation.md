# Repository automation

The automation follows patterns reviewed in the local headroom, PatternKit,
StableDiffusionStudio, JD.AI and JD.Efcpt.Build repositories, adapted for this
Python coordinator and its Docker workers.

- CI runs unit tests on Linux and Windows with Python 3.12–3.14, builds and
  checks wheel/source packages, smoke-tests the installed CLI outside the
  source tree, and runs deterministic Docker worker end-to-end tests.
- Security runs CodeQL, Python dependency auditing, high-severity Bandit checks
  and the worker npm dependency audit weekly and on PRs.
- Dependabot proposes Python, worker npm, Docker and Actions updates weekly.
- All third-party Actions are pinned to full commit IDs resolved from their
  upstream release tags; Dependabot keeps those pins up to date.
- Release validates tests, worker integration and package metadata before
  publishing a matching `v<project version>` tag to GitHub Releases and a
  versioned worker image to GHCR. Manual dispatch validates only. Publishing
  uses the `release` environment and publishes the same worker image tested by
  qualification, without rebuilding source in the privileged job. Manual runs
  cannot publish even when dispatched on a tag. No production coordinator service or hosting
  target is currently defined; deployment consists of distributing these
  installable artifacts. Operators retain local configuration and credentials.
- Tagged releases attach wheel/source packages, an example configured for the
  matching versioned worker image, an installation guide and SHA-256 checksums.
  Stable tags publish both the versioned GHCR image and a `latest` alias; the
  coordinator and image share a version. Source/revision OCI labels link images
  to this repository. `scripts/prepare_release.py` derives all generated links
  from the active GitHub repository and project version, including after transfer.
  A compressed copy of the tested worker image is also attached to the public
  release for direct download/offline loading; its digest is in `SHA256SUMS`.
- Issue forms and the PR template collect reproduction and validation evidence.
  Run the manual Sync labels workflow to create/update the declared labels;
  it preserves unrelated labels. PRs receive documentation, CI, test, dependency
  and security labels based on changed paths. Sync labels should run before
  enabling automatic labeling.

## GitHub activation

The initial destination is `JerrettDavis/HeadroomAgentOps`. Repository settings
live centrally in `.github/repository.json`; workflow package names and GitHub
API targets derive from the current repository context. A future organization
transfer does not require editing workflow URLs or registry names. Update the
fallback repository and release reviewers in that JSON file after a transfer.

Run `python scripts/configure_repository.py` to preview settings, then pass
`--apply` to configure labels, security features, default workflow permissions,
main-branch protection and a reviewer-gated release environment restricted to
`v*` tags. An explicit `--repository owner/name` takes precedence, followed by
GitHub environment context, the current remote, and the central fallback.

Enable Actions. Set the default branch to main (or master, both supported).
Configure branch protection to require all CI matrix jobs, package, e2e and
security checks. Require PR review and resolved conversations; restrict bypasses.
Enable dependency graph, Dependabot alerts, secret scanning/push protection and
private vulnerability reporting where available. CodeQL requires GitHub Code
Security for private repositories. Configure the `release` environment with
required reviewers and tag restrictions before publishing tags. Enable GHCR
package writes for the workflow token. Run Sync labels once.

For the first GHCR publication, open the worker package from the repository's
Packages list and change its package visibility to public. GitHub initially
creates GHCR packages as private, even when the linked repository is public.
Confirm an anonymous `docker pull` afterward. Public release image archives
remain available independently through GitHub Releases. After an organization
transfer, verify package ownership/access and public visibility again.

Release artifacts and images are publication outputs, not authorization to run
review or publication against an upstream repository. No model credentials or
upstream GitHub publisher tokens are needed by CI. PR workflows execute with
read-only contents permissions. The PR labeler uses `pull_request_target` to
support forks, with only contents-read and PR-write permissions; it reads trusted
base-branch configuration through the API and never checks out or runs PR code.

Hosted execution, branch protection, environment approval rules and actual label
creation must be verified after a GitHub destination exists. Local checks cannot
prove those remote settings.

## Local validation (2026-11-11)

All 157 unittest cases passed with `AGENTOPS_DOCKER_TESTS=1`, including four
real-container tests. Actionlint passed for all workflows. Wheel and source
distribution builds and Twine checks passed; an isolated wheel installation
ran `headroom-review --help` outside the checkout. Python dependency auditing
and the worker npm audit reported no known vulnerabilities. Bandit reported
zero high-severity findings after a specific B324 suppression documenting
Git's required SHA-1 blob format; 21 low and two medium findings remain outside
that configured severity gate. Linux/Python matrix runs and CodeQL require
hosted execution and have not been run locally.
