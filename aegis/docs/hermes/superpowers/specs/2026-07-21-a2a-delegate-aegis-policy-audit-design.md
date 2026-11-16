# A2A delegate Aegis policy and audit

## Scope

When `AEGIS_BOOTSTRAP_ADMIN_PASSWORD` is configured, authorize each valid
`aegis_a2a_delegate` call against rules stored in the active profile's `aegis.db` and
record one final audit row. Add administrator CRUD for Agent Policy rules and a
read-only, searchable Audit Logs page. Existing delegation behavior remains
unchanged when the environment gate is absent.

## Storage and policy semantics

A new `tools/a2a_delegate_aegis.py` module owns SQLite initialization, policy
storage and matching, audit writes, and audit queries. It depends only on the
standard library and `get_hermes_home()` so delegation does not require the
Aegis web service to be running.

The `policy` table stores a unique positive integer `rank_id`, `platform`,
`user_id`, `agent_name`, and an `allow` or `deny` status. Empty selector values
normalize to `*`. A rule matches when every non-wildcard selector matches the
call; platform is normalized to lowercase while user and agent identifiers are
exact. The lowest matching `rank_id` decides. No match allows delegation.

The `a2a_delegate_audit` table stores an id, UTC timestamp, caller identity,
target agent, complete goal, the original remote session-id argument, loop and
output flags, and the authorization-check result. New records use `succ` when
the policy check allows delegation and `fail` when policy denies delegation or
the check itself errors. Existing `auth_denied` rows and filters remain readable
for backward compatibility, but new checks do not write that status. An empty
session-id argument remains empty in the audit even though the existing remote
session implementation may generate its own context id.

## Delegation integration

After existing parent, goal, and agent-name validation, `aegis_a2a_delegate` checks
the environment gate. With the gate disabled it follows the current path and
does not open `aegis.db`. With the gate enabled it evaluates policy before
registry lookup or remote network activity. A matching deny logs a warning,
writes a failed authorization audit, and returns a structured authorization
failure. A policy read error also writes a failed authorization audit when the
database is available, fails closed, and exposes only a generic error to the
model.

An allowed check writes a successful authorization audit before the existing
delegate implementation runs. Remote delegation success, failure, interruption,
or exception does not modify that row and does not create another audit row.
Audit writes are best-effort: failures are logged but never replace the
authorization decision or delegation outcome. Each structurally valid invocation
produces at most one authorization audit row.

The Aegis integration is deliberately non-executing. The tool calls
`run_aegis_checked_delegate(...)` to obtain an authorization flag, status, and
failure payload. That function owns the authorization audit write but accepts no
callback and never invokes remote delegation. The tool invokes the existing
remote delegate itself only when allowed and makes no later audit call.

## API and user interface

The administrator-only routing API gains Agent Policy CRUD at
`/api/routing/agent` and `/api/routing/agent/{rank_id}`. Updates may change the
rank, with duplicate ranks returning conflict. The existing Policy page gains
Global Routing Rules and Agent Policy sub-tabs; Agent Policy supports search,
refresh, and modal create/edit/delete operations.

The administrator-only `GET /api/audit/a2a-delegates` endpoint returns audit
rows ordered newest first with total-count, page, and page-size metadata. It
supports independent, conjunctive filters for every stored field plus UTC time
range. The enabled `/audit` page exposes those filters, fixed 50-row server-side
pagination, refresh/reset controls, and expandable long goals. Audit data is
read-only; deletion, export, and retention are outside this change.

## Failure handling and verification

SQLite access uses parameterized statements, short transactions, and a busy
timeout. Schema initialization is additive and must preserve the existing
users table. Backend tests use a real temporary `aegis.db` to cover schema,
CRUD authorization, wildcard and rank matching, fail-closed reads, audit
persistence, filtering, and pagination. Tool tests cover the environment gate,
deny short-circuit, caller metadata, empty audit session ids, authorization
statuses, and best-effort audit failure. Frontend tests cover both Policy sub-tabs,
Agent Policy CRUD interactions, the administrator Audit route, filters,
pagination, reset, and goal expansion.
