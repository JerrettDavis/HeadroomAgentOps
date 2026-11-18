# Provider allowances

The dashboard's **Provider allowances** panel shows account usage separately from
the token and spend report. Codex and Claude readings describe subscription
allowance, including weekly and shorter windows. Copilot readings describe the
entitlements its account endpoint actually reports; monthly entitlements are
labeled monthly. Local review token totals never estimate weekly allowance.

Open **Review stop point** on a provider card to change its percentage used.
The default is **80%**. At exactly 80%, new reviews wait. The same stop point
protects other reported allowance windows. Changes are stored in the shared
ledger and apply to manual CLI runs, automatic reviews and resumed saved plans.
Each provider has its own setting.

Running reviews finish and publish through the existing qualification gates.
Waiting jobs stay queued without consuming an attempt. Coordinators check again
while waiting and resume when a fresh reading is below the stop point, or when
the saved threshold permits work. A limit pause does not stop the run.

## Unknown usage and overrides

Enforcement waits for a fresh, authenticated weekly reading. An unavailable
provider, expired reset time, stale reading, account mismatch, or missing weekly
window pauses new work. Last-known values remain visibly marked. A passed reset
time triggers a new reading; it never implies zero usage.

A Copilot account may expose only monthly premium-request usage. That card shows
the monthly reading and **Weekly unknown**. It cannot authorize weekly-protected
reviews until the provider supplies a weekly window. Claude requires the same
subscription credentials used by the worker; an unrelated ambient OAuth token
is not substituted.

Clearing **Enforce limits** explicitly bypasses the allowance gate for that
provider. The card then says **Enforcement off**. This permits work when the
provider does not expose weekly usage, but does not assert that usage is low.
Codex's subscription-only authentication policy remains enforced even with this
override: ambient API keys or API-key authentication files cannot enable billed
API reviews. There is no automatic provider or authentication fallback.

## Configuration and data sources

Optional initial defaults in `agentops.toml`:

```toml
[provider_limits.codex]
stop_percent = 80
enabled = true

[provider_limits.claude]
stop_percent = 80
enabled = true
```

Saved dashboard settings take precedence over these defaults. The active
reviewer and the most recently configured session for other providers appear in
the dashboard; additional provider tables also make those providers visible.

Readers make bounded account requests without starting model inference:

- Codex: local CLI app-server `account/rateLimits/read`. Its response account
  identity must match the worker's subscription credentials. Verified with CLI
  0.167.1; a CLI without account identity in the response cannot authorize work.
- Claude: account usage at Anthropic's fixed OAuth usage endpoint, using the
  same `.credentials.json` file as the worker.
- Copilot: account entitlements at GitHub's fixed account endpoint, using the
  configured scoped token or native sign-in. This may expose monthly
  entitlements without a weekly window.
- Devin: account plan status read from the same service the CLI uses, with the
  worker's own `credentials.toml` sign-in. Daily/weekly quota and monthly
  ACU, spend and prompt-credit limits are used when the account reports them.
  Quota-billed accounts may expose only reset times; set
  `provider_limits.devin.token_budget` to enforce a measured weekly token
  budget instead. Consumption is summed from this ledger's own execution
  records (`input_tokens` + `output_tokens`) since the account's weekly reset,
  so it reflects only reviews this coordinator ran. Without reported windows
  or a configured budget, enforcement pauses dispatch (fail closed).

Any provider may additionally set `token_budget`: a measured weekly consumption
cap summed from this ledger's own execution records (`input_tokens` +
`output_tokens`), bounded by the provider's reported weekly reset. It enforces
local efficiency even when account quota is generous or unreported. Providers
that report no weekly window cannot bound the budget and do not gain a window
from it.

Provider response shapes can change. Unsupported or malformed data is shown as
unavailable and cannot silently authorize a review. Allowance status refreshes
independently of review-inventory polling. Readings are cached for at most 60
seconds and refreshed after reset; a shared refresh lease prevents concurrent
coordinators from flooding a provider. Failed requests have a cooldown. Account
changes invalidate observations.

Only normalized usage windows, reset times, freshness and opaque credential
fingerprints are saved. Provider credentials and raw account errors are never
exposed by the dashboard. Before a worker starts, both its admitted reading and
current credentials must match the credentials actually staged for that worker.

References: [Codex app-server](https://learn.chatgpt.com/docs/app-server),
[Claude usage windows](https://code.claude.com/docs/en/statusline), and
[Copilot account quota](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/usage-and-billing).
