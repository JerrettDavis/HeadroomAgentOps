# Dashboard maintenance

## PR updates

Open **PR updates** and refresh to inspect every open PR targeting main that is behind or whose comparison is unknown. Drafts, forks and excluded authors remain visible. Each row shows drift, known conflict and permission status, exclusions and the latest maintenance outcome.

Select individual PRs or **Select all outdated PRs**, then choose **Update selected PRs**. AgentOps performs real rebases in isolated checkouts and pushes with an explicit lease on the observed author head. A changed head, main revision, target, permission failure or conflict retains its reason in **Needs remediation**. Successful rows do not depend on other rows succeeding. Inventory refreshes after the batch.

Use the secondary queue to retry a mechanical update or explicitly send selected conflicts to the configured coding agent. Agent remediation retains separate attempts and validation. It never labels substantive edits as mechanical or suppresses their ordinary review requirements. Changes to protected contributor workflow/tooling remain held under the existing publication policy.

Verified mechanical receipts prevent extra AgentOps source reviews caused solely by rebased commit IDs. Existing review evidence and substantive discussion remain authoritative. A later author edit or new concern still triggers normal handling. This scheduling reuse does not fabricate approvals or bypass publication/CI gates.

**Reapprove previously approved workflows** continues only recorded successful AgentOps approvals with an unambiguous PR/run association and unchanged reviewed execution source. It binds the PR, head and target refs, source repository, event, full workflow source ref and protected source-tree identity. It also recognizes approvals from preceding maintenance updates. Unrecorded manual approvals, ambiguous fork associations, changed source or active policy holds require manual workflow approval. Late-arriving runs are checked in the background for up to 24 hours; terminal entries stop polling.

## Main CI

**Main CI** monitors upstream main independently of the browser. It separates execution contexts, latest attempts, pending checks, approval requirements and API errors. An incident retains all relevant workflow coverage it observed; a pending new main or incomplete success does not clear a previous failure.

The default is an in-app alert with **Fix main**. **Autofix main CI** starts the same repair automatically and suppresses interrupting alerts while preserving visible status. **Watch main CI** controls this independent monitor. Settings, incident identity and PR receipts persist across restarts.

Repair agents receive redacted failure evidence in the existing isolated worker. The host imports a verified candidate bundle and reruns recorded checks in a credential-free, networkless container against an immutable candidate snapshot before publication. It checks current main and failure state again, pushes a dedicated branch, and opens a repair PR for human review. It never merges the repair PR.

Protected main workflow/tooling changes retain their candidate, source audit and validation instead of silently bypassing execution controls. **Review protected source repair** shows the exact source diff and validation; **Publish this reviewed source repair** authorizes only the displayed candidate and source manifest. Active publication/security holds still apply. Creating a branch or PR can itself start Actions, which is why this decision precedes publication for protected changes.

Repeated clicks and restarts reconcile recorded branches and open, closed or merged PR receipts. An ambiguous PR-creation outcome is retained for reconciliation rather than blindly creating another PR. Explicit retry starts a fresh agent attempt only after the previous owned execution is confirmed terminal and remote effects have been checked.

Maintenance obeys publication settings, author exclusions, exact-source CI policy and active security/policy holds. Its queues are separate from review admission. Guarded reload rejects active maintenance operations and serializes with maintenance admission and approval writes.

## Validation scope

Repository tests use controlled Git/GitHub fixtures and deterministic harnesses, including real local Git rebases, leases, candidate bundles, remote branch writes and actual Docker execution. Production PRs are not rebased, arbitrary workflows are not approved, and production main is not deliberately broken merely to test these controls. Deployment acceptance records distinguish fixture proof from live dashboard and read-only GitHub observations.
