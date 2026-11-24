# PR auto-stacker

Use **Merge priority** to select eligible PRs, then **Prepare selected PRs**.
Prerequisites are included automatically and shown in the saved stack. Preparation
does not write to GitHub. Once the combined revision is prepared, **Publish draft
PR** creates a dedicated branch and one combined draft PR. Review its actual
combined CI, mark it ready, and merge it on GitHub yourself. The application never
merges, enables auto-merge, bypasses protection, or changes contributor branches.

Preparation makes **zero model calls**. Dependency parsing, stable topological
ordering, pinned-ref checks, Git merges, validation and publication reconciliation
are algorithms. It does not ask an agent to rank compatibility or resolve a
conflict. For the same pinned members, target and validation settings, repeated
requests reuse a durable result. Requests use one preparation operation at a time,
one batched fetch per preparation, and at most four concurrent read requests.
Ordinary dashboard polling reads local saved state; it does not invoke agents or
refetch every PR. Publication and explicit **Recheck stack** obtain fresh evidence.

## Eligibility and integration

Source PRs must be open, non-draft, approved, have passing CI, and have verified
mergeability and branch freshness. Unknown evidence blocks preparation. Explicit
`Depends on #123`, `Stacked on #123`, and `Requires #123` declarations use the same
parser as merge priority; verified base/head branch relationships also establish
dependencies. Quoted or fenced examples are ignored. Missing, ambiguous or cyclic
prerequisites block the stack. Dependencies in other repositories must already be
verified merged. A stack can include at most 100 PRs including prerequisites.

Each preparation pins the target and every source SHA. Git fetches all source
refs in one operation and verifies them against the captured identities. It then
merges them cumulatively in prerequisite order, with ascending PR number as the
stable tie-breaker. Merge commits preserve original source commits and authors.
This checks actual compatibility, including nonoverlapping edits within one file.
Deterministic commit metadata makes the resulting revision reproducible for the
same inputs. A conflicting member stops preparation, identifies affected paths,
and never becomes publishable. Fix the original source PR and prepare again.

## Optional local validation

In `agentops.toml`, configure argv arrays, not shell strings:

```toml
[stacking]
commands = [["python3", "-m", "unittest", "discover", "-s", "tests"]]
timeout_seconds = 900
```

These commands are specific to the repository being stacked. Omit them if its
GitHub workflow is the intended combined validation. Missing local commands show
**unconfigured**, never **passed**. A conflict-free stack without configured local
validation may be published as a draft to obtain GitHub CI. Local failures or
timeouts block publication. Source CI is displayed separately from combined
validation and never implies the aggregate has passed CI.

Validation uses the configured worker `image`, resolved to its immutable image
ID. It overrides the worker's agent entrypoint and runs commands directly as a
non-root user with no network, model auth, GitHub credentials, inherited host
environment, or inherited tools. The container has a read-only root, resource
limits and bounded execution time. Commands operate on a separate checkout;
their edits cannot enter the published revision. Dependency installation that
requires network access is intentionally unavailable; use a suitable prebuilt
image or rely on GitHub CI. Up to 20 commands run sequentially and stop on the
first failure; each retains only its last 16 KB of output. Timeouts remove the
container explicitly, rather than assuming that killing the client stopped it.

## Publication, freshness and recovery

Publication requires `publish = true`, the existing loopback/same-origin dashboard
authorization, repository write permission, and a separate human click. The
service rechecks source heads, dependencies, approvals, CI, target and validation
settings immediately before publication. Changed or unavailable evidence blocks
the action. Saved evidence expires after three minutes in the dashboard; use
**Recheck stack** to renew it. Final merge authorization and CI remain GitHub's
responsibility on the combined PR.

The only pushed branch is `agentops/stacks/<stack identity>`. A different existing
revision is rejected, never overwritten or force-pushed. The draft PR includes
the integration order, source SHAs, attribution, target and combined revision.
After a lost response, **Recheck stack** and **Recover draft publication** inspect
the existing branch and PR before attempting another write. Do not assume that a
browser timeout stopped an operation; its durable record is authoritative.

Stack records live in a separate table in the existing SQLite ledger. Checkouts
live under the state directory's `stacks/`; interrupted checkouts are retained for
inspection. A confirmed exited process marks unfinished preparation interrupted
or publication uncertain. Unknown process ownership is held instead of restarted.
Use **Prepare again** for interrupted local work. No dashboard restart, external
write, or merge happens merely because a saved record exists.

After the combined PR merges, **Recheck stack** reads source PR states and lists
still-open PRs for manual cleanup. GitHub may recognize sources as merged when
their commits remain reachable; squash or rebase merging can prevent that.
The application does not close source PRs automatically.

## Verification

Run `python -m unittest discover -s tests` and the Playwright suite in `tests/ui`.
Set `AGENTOPS_DOCKER_TESTS=1` for real isolated-container checks using the local
worker image. Stack tests use temporary real Git repositories and a simulated
GitHub transport; they do not call a model or write to a real GitHub repository.
