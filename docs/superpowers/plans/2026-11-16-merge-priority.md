# Merge priority implementation plan

> Execute inline with superpowers:executing-plans; use TDD for ranking and collection.

**Goal:** A live, linked merge order with visible PR stacks.
**Architecture:** A read-only GitHub collector supplies a background, bounded-age
cache independent of review inventory. A pure ranking function builds dependency
relationships and readiness reasons. The existing panel serves the result and a
separate dashboard view renders it with the existing theme and polling controls.
**Tech stack:** Python standard library, existing GitHub CLI transport, HTML/CSS/JS.
**Spec:** ../specs/2026-11-16-merge-priority-design.md

## Constraints and review focus

No changes to automated review qualification, author exclusion or publication.
Unknown CI, unknown ancestry, stale data and failed refreshes cannot imply ready.
Dependencies precede dependants; cycles and missing references remain visible.
Untrusted PR strings must be escaped and links limited to GitHub HTTPS URLs.
Keep existing dashboard themes, independent scrolling and keyboard access.

## Tasks

1. [x] Add `tests/test_merge_priority.py`, run failing tests, then implement
   `agentops/merge_priority.py`: `rank_prs(repo, prs)` returns rows in suggested
   order with category, readiness, dependency links and canonical display parent.
   Cover maintainer/deps/main-CI/fix/feat, drafts, missing CI, behind/conflicts,
   stacks, forks, multiple prerequisites, missing references and cycles.
2. [x] Test then add `collect_priority(github, comparisons=None)` with paginated GraphQL open PRs,
   current commit rollup, author association and default/base commit identities;
   verify ancestry using immutable compare endpoints. Test collection failures
   and incomplete inventory. Add `PriorityMonitor.status()` with one background
   refresh, cache age and explicit failure state; test refresh and concurrency.
3. [x] Add `/api/merge-priority` in `agentops/panel.py` with injectable monitor
   for fixtures. Add themed Merge priority/Reviews view controls, nested linked
   rows, reasons, refresh time/error state and polling in a focused UI asset.
   Add API and browser tests for refresh, stacks, errors, escaping and layouts.
4. [x] Run Python and browser suites, inspect rendered desktop/mobile themes,
   review the whole diff, document ranking and limits in README, and commit.

## Execution decisions

The user asked to continue after the concrete design was presented; proceed
inline in the existing isolated worktree. Use author association OWNER/MEMBER/
COLLABORATOR as visible maintainer evidence. Main-CI repair is a declared category
(explicit label or declaration), not a claim that causality has been verified.
Retain the established Segoe UI type and theme tokens. Use numbered merge order
and connecting branch lines as the principal visual cue, with compact status
badges and inline blocker text.

## Verification ledger

- Red/green regression cycles covered ranking, unknown evidence, stack topology,
  ambiguity, historical base OIDs, observation expiry and immutable compare reuse.
- All 317 Python cases passed (4 Docker skips); all 23 browser tests passed.
- Wheel and sdist built and passed twine checks; the wheel contains the collector,
  page and JS asset.
- Independent review identified category/adjacency ordering and ambiguous branch
  parents; both were reproduced, fixed and re-reviewed without important findings.
- Live collection completed across 181 PRs; the deployed API subsequently
  displayed 179 PRs. Current branch ancestry correctly prevents false readiness
  caused by historical PR base OIDs.
- Desktop dark and mobile light views were inspected; deep nested stacks,
  multiple prerequisite links, keyboard access and narrow overflow were tested.
- Reloaded only the live panel process; coordinator PID 193352 and four active
  review workers remained running. Panel PID is 254684 on port 8765, using the
  existing configuration and state directory.
