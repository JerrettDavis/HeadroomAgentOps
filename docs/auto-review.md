# Automatic PR reviews

Start the dashboard with `python -m agentops panel` and use the three controls under
**Automatic reviews**:

- **Review new PRs automatically** schedules PRs first seen after the initial inventory.
- **Review updated PRs automatically** schedules a PR when its head commit changes.
  Comments, review activity, and CI updates do not trigger another review.
- **Only review non-draft PRs** keeps draft PRs waiting until they become ready.

The first complete inventory establishes a baseline without reviewing the existing
backlog. The server polls every minute while either review trigger is enabled.
Settings, observed heads, and pending events persist in `auto-review.json` in the
configured state directory. Restarting the dashboard preserves them. Turning both
triggers off pauses observation; changes detected after resuming can be reviewed.
A pending event remains available while its trigger is disabled. Closed PRs are
removed on the next complete inventory. Incomplete inventories never dispatch work.

Scheduling inventories verify membership, heads, draft status, targets, authors,
and head repositories. Unrelated comment/CI timestamp changes do not invalidate
queue selection. Preview and launch revalidate the selected PRs directly, so new
arrivals elsewhere cannot repeatedly cancel an already selected batch. Workers
still collect fresh detailed discussion and CI evidence before reviewing or publishing.

Reviews use the server's configured harness, model, publication setting, author
exclusions, and worker concurrency. Automatic batches wait for coordinators launched
by this dashboard to finish. The shared ledger enforces worker capacity and avoids
automatically dispatching a head already present in a review job. Existing manual
review controls remain available for retries or reviewing the initial backlog.

The page shows whether auto mode is enabled, the last successful inventory time,
and polling errors. The dashboard server must remain running; the browser tab may
be closed. Settings updates require the same local origin and launch token as
manual review launches.

Dependabot PRs never receive change-request reviews. Workers prepare and test
maintainer fixes and propose candidate pushes through the existing publication
checks. Unresolved repair blockers remain visible rather than approving an unfixed
defect or asking Dependabot to repair it. No automatic merge is performed.
