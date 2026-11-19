# Automatic PR reviews

Start the dashboard with `python -m agentops panel` and open **Automatic reviews**
from the header icon:

- **Review new PRs automatically** schedules newly observed PRs and unreviewed required-review backlog.
- **Review updated PRs automatically** checks the PR contribution when its head commit changes.
  With verified prior analysis, unchanged contributions (including main updates
  and mechanical conflict resolutions) and dependency-only updates skip agent work.
  Hunk line numbers may move; function anchors and surrounding context must match.
  Python formatting is compared using complete hash-verified source tokens, preserving
  strings, indentation and logical newlines. Missing or truncated evidence requires review.
  CI and discussion updates are rechecked against saved analysis; they do not
  automatically start an unlimited sequence of fresh reviews.
- **Only review non-draft PRs** keeps draft PRs waiting until they become ready.

The first complete inventory establishes a baseline for ordinary PR events, while
eligible `REVIEW_REQUIRED` PRs are caught up immediately. Previously attempted
required reviews use the updated-PR toggle; never-reviewed PRs use the new-PR toggle.
The server polls every minute while either review trigger is enabled.
Settings, observed heads, and pending events persist in `auto-review.json` in the
configured state directory. Restarting the dashboard preserves them. Turning both
triggers off pauses observation; changes detected after resuming can be reviewed.
A pending event remains available while its trigger is disabled. Closed PRs are
removed on the next complete inventory. Incomplete inventories never dispatch work.

`review_notes` in `auto-review.json` records the current head, source review job,
comparison reason and immutable evidence references across sessions. A skipped
update never grants approval, test coverage or CI qualification to the new head.
The baseline remains the last actual analysis, so multiple small updates are
compared cumulatively. Existing unresolved findings remain in the prior evidence.
Dependency-only handling covers modified standard lockfiles and changes confined
to dependency fields in `package.json` and `pyproject.toml`; scripts, build settings,
renames, removals and mixed code updates require review.

Use **New Review** to explicitly re-review a PR regardless of automatic skips.
Manual launches use `--force-fresh`; CLI runs can also pass that flag. For material
updates, workers receive `review-delta.json` and `previous-review-notes.json` and
reuse source-reading coverage only for verified identical Git blobs. Full prior
evidence remains available for targeted lookups. Main interactions, unresolved
findings and current validation still require attention.

Queue discovery completes one paginated traversal without requiring the whole
repository to remain unchanged for a second pass. Preview and launch independently
revalidate selected heads, draft status, targets and current main, so new
arrivals elsewhere cannot repeatedly cancel an already selected batch. Workers
still collect fresh detailed discussion and CI evidence before reviewing or publishing.

Reviews use the server's configured harness, model, publication setting, author
exclusions, and worker concurrency. Automatic batches wait for coordinators launched
by this dashboard to finish. The shared ledger enforces worker capacity. A stale
job or comment-only review does not complete a required review: auto runs require
a qualified approval or supported change request. Prior analysis is retained for
qualification follow-ups. Completed mandatory attempts that still lack a decision
are shown as unresolved holds rather than rerun in an unlimited model loop.
Existing manual review and requalification controls remain available for resolving
these holds. A new head or main permits a fresh required-review attempt.

Failed workers retry with an increasing delay, starting at five minutes and
capped at one hour for the same head/main. Successful or active work takes
precedence over old failures. Other PRs and new revisions remain eligible.

Every five minutes a single background task rechecks up to four outstanding
current-revision reviews without blocking inventory polling. Attempt timestamps persist independently of success so malformed records
cannot starve later reviews. Existing gates can release qualified approvals when
CI completes; new relevant evidence can queue a follow-up within the original
attempt budget. Resumption revalidates every queued PR's head, main, draft status,
author exclusions, and saved/current publication authorization. Ineligible queued
jobs are withdrawn atomically without cancelling already running workers.

Workers must distinguish reused analysis from newly executed tests. Readiness
still requires an actual passing test in the current session; reused artifacts
must not be relabeled as fresh results. Qualification and publication guards are
unchanged by automatic reconciliation.

The page shows whether auto mode is enabled, the last successful inventory time,
eligible work, unresolved holds, and polling errors. The dashboard server must remain running; the browser tab may
be closed. Settings updates require the same local origin and launch token as
manual review launches.

Dependabot PRs never receive change-request reviews. Workers prepare and test
maintainer fixes and propose candidate pushes through the existing publication
checks. Unresolved repair blockers remain visible rather than approving an unfixed
defect or asking Dependabot to repair it. No automatic merge is performed.
