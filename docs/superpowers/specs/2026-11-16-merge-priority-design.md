# Live merge priority view

The dashboard should give contributors and maintainers an immediate suggested
merge order, with links to the PRs and visible stack dependencies. This is a
read-only recommendation view; existing review and publication policy remains
independent of its ranking.

## Proposed behavior

- Add a Merge priority view using the existing Python panel and browser polling.
- Show all open PRs, including maintainer PRs excluded from automated reviews.
- Put non-draft PRs with passing CI and verified freshness first. Show drafts,
  failing or pending CI, conflicts, stale branches and missing evidence with
  explicit reasons outside the ready group.
- Within equivalent readiness, rank maintainer PRs, dependency updates, fixes
  for main CI, other fixes, features, then unclassified changes. Use stable
  tie-breaking so polling does not shuffle equal candidates.
- Show the reason for each category. Maintainer identity must come from
  repository ownership or collaborator metadata. Main-CI fixes need explicit
  evidence or a repository-defined label; a generic fix title is insufficient.
- Link every PR to GitHub. Render stacks as nested PRs with links to their
  prerequisites. Respect prerequisites before category preference in suggested
  merge order; flag blocked stacks rather than calling their children ready.
- Infer stacks from verified base/head branch relationships and explicit
  dependency declarations, not arbitrary PR mentions. Handle cycles, missing
  prerequisites and ambiguous references visibly without dropping PRs.
- Update independently of the currently selected review session. Display the
  observation time and refresh errors; cached evidence must not silently become
  current evidence after a fetch failure.

## Current evidence

The dashboard is implemented in `agentops/panel.py` and `agentops/panel.html`,
with browser coverage in `tests/ui/dashboard.spec.cjs`. GitHub inventory queries
in `agentops/github.py` already collect PR titles, bodies, URLs, authors,
head/base branches, draft status, merge state and review decisions. Maintainer
classification and current CI evidence still need their data sources verified.

## Verification required

Exercise readiness and category ordering, maintainer inclusion, unknown and
stale evidence, failed refreshes, and parent-before-child ordering including
multi-level stacks and cycles. Browser verification must cover GitHub links,
visible nesting and blockers, automatic refresh, keyboard access and narrow
layouts. Run the relevant existing panel and inventory checks for regressions.

## Implementation notes

The user asked to continue after this proposal. The implementation uses the
existing application stack. A nested stack outline preserves the hierarchy while
the separate numbered list follows category priority and prerequisite order.
Refreshes run at most once per minute, with one collection at a time. Observations
expire after 180 seconds; failed refreshes invalidate readiness immediately.
Immutable commit comparisons are cached; PR metadata and CI are fetched anew.
