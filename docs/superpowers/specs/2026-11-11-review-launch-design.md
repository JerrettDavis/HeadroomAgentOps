# Dashboard review launch

Extend the existing loopback reviewer panel with a three-step wizard: select,
preview, launch. Accept one PR, an explicit batch of numbers/URLs, all open PRs,
or a GitHub search query scoped to the configured repository. A preview displays
the exact PRs and review settings before dispatch. Users can deselect query
results. Launch must use the existing immutable plans and CLI coordinator, with
publication off by default and one isolated session per PR.

Use Python standard-library HTTP and the existing panel HTML. Preserve read-only
GET routes. Protect mutations with same-origin checks and a per-server token.
Validate selections and settings server-side; reject incomplete search results,
empty batches, foreign URLs, and changed PR heads between preview and launch.
Make repeated launch requests for one preview return the same run. Track the
coordinator process and expose exit failures, retaining its log in the state
directory. New runs appear in the existing dashboard. Viewing requires no model
credentials; launching uses the existing host configuration and credential flow.

Verification covers selection, query pagination, launch idempotency, stale heads,
HTTP protection, coordinator arguments, existing panel behavior, and browser flow.
Land the verified implementation on origin/main.
