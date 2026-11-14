# Automatic PR reviews

Start the dashboard with `python -m agentops panel` and use the three controls under
**Automatic reviews**:

- **Review new PRs automatically** schedules newly observed PRs and unreviewed required-review backlog.
- **Review updated PRs automatically** schedules a PR when its head commit changes.
  Comments, review activity, and CI updates do not trigger another review.
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

The page shows whether auto mode is enabled, the last successful inventory time,
eligible work, unresolved holds, and polling errors. The dashboard server must remain running; the browser tab may
be closed. Settings updates require the same local origin and launch token as
manual review launches.

Dependabot PRs never receive change-request reviews. Workers prepare and test
maintainer fixes and propose candidate pushes through the existing publication
checks. Unresolved repair blockers remain visible rather than approving an unfixed
defect or asking Dependabot to repair it. No automatic merge is performed.
