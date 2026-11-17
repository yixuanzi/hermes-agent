# Aegis Backend

The standalone Aegis backend is a small FastAPI service that exposes the Aegis
management APIs without depending on the frontend dev server. It is the local,
file-backed backend for agent registration and global routing rule management.

## Start The Server

Run the backend directly from the repo root:

```bash
source .venv/bin/activate
python aegis/backend/main.py
```

Or use the Hermes entrypoint:

```bash
hermes aegis
```

### Hermes home

On startup, Aegis binds `HERMES_HOME` to the Hermes home directory of the
agent/profile that launched it. It also uses that directory as the process
working directory and sets `HOME` to `<HERMES_HOME>/home`, matching AISOC's
runtime isolation. This keeps Aegis data, configuration, sessions, skills,
and A2A registry scoped to the launching agent instead of the shell's current
directory.

Use a Hermes profile to select that agent home:

```bash
hermes -p incident-response aegis
```

For direct Python startup, pass the same profile explicitly:

```bash
python aegis/backend/main.py --profile incident-response
```

For a custom agent home, set `HERMES_HOME` before launching:

```bash
export HERMES_HOME="/path/to/agent-home"
hermes aegis
```

The selected home is printed at startup as `Using Hermes home: …`.

Default bind settings:

- Host: `127.0.0.1`
- Port: `9130`

Useful flags:

```bash
python aegis/backend/main.py --host 127.0.0.1 --port 9130
python aegis/backend/main.py --no-open
python aegis/backend/main.py --host 0.0.0.0 --port 9130 --insecure
```

The last form intentionally exposes the API on the network and requires
`--insecure`.

## Authentication

The backend now uses username/password login plus JWT bearer auth for API and
WebSocket access.

Optional environment:

```bash
export AEGIS_JWT_SECRET="replace-me-with-a-long-random-secret"
python aegis/backend/main.py
```

Initial admin bootstrap:

```bash
export AEGIS_BOOTSTRAP_ADMIN_PASSWORD="choose-a-strong-password"
hermes aegis
```

The first `admin` account is created only when this secret is present. If it
is missing, `/api/system/bootstrap` reports that setup is required and login
remains rejected; there is no anonymous admin registration or fixed default
password.

Login response returns an access token. Protected endpoints expect:

```text
Authorization: Bearer <jwt_access_token>
```

The exception is the public, read-only skill JavaScript route described below;
it exposes only regular `.js` files under the repository's `aegis/skills/`
directory for generated HTML previews.

The frontend login page also uses the same JWT for `/api/chat/ws?token=...`.

### Token revocation

Access tokens are minted in one place (`build_token_payload`, used by password
login and the SSO ticket exchange) and carry two revocation claims:

- `ver` — the user's `token_version` (a `users` column, default 0, added to
  existing databases at startup). It is bumped when the user changes their own
  password, when an admin resets their password, demotes their role
  (admin → operator → user) or disables them. A token whose `ver` differs from
  the current version is rejected with 401 on HTTP and with close code 4401 on
  `/api/chat/ws`. A token without `ver` counts as version 0.
- `jti` — `POST /api/auth/logout` revokes just the presented token by storing
  its `jti` with its `exp` in the `revoked_access_tokens` table of `aegis.db`;
  rows past their `exp` are pruned on the next revocation. A token minted
  without `jti` cannot be revoked alone, so logging out with it bumps the
  user's `token_version` instead.

Logout stays a public route: a missing, expired or already-revoked token simply
returns `{"logged_out": true}`. The console calls it on sign-out, and treats a
password change as signing out everywhere (the current session included).
Revocation is checked when a request or websocket connection authenticates; an
already-open chat socket is not closed.

## App Entry

The authenticated `GET /api/app-entries` endpoint loads the current
organization's active and expiring subscription entries from Aegis Portal.
Configure the Portal URL, organization code, and server-only public API key in
the Aegis process environment:

```dotenv
AEGIS_PORTAL_URL=http://127.0.0.1:8080
AEGIS_ORG_CODE=XINGHAI-SH
PUBLIC_SUBSCRIPTION_API_KEY=<server-side-secret>
```

`AEGIS_PORTAL_URL` is the Portal base URL. `PORTAL_BASE_URL` is also accepted
as a compatibility fallback. Aegis sends `PUBLIC_SUBSCRIPTION_API_KEY` to the
Portal as `X-API-Key`; the key is never sent to the browser. App Entry uses the
Portal's `sub_app_entry_url` when available and falls back to `sub_entry_url`.

## Aegis Portal OIDC

The standalone web app is an OIDC client of Aegis Portal. Configure the
following values in the process environment; never commit the client secret:

```dotenv
OIDC_ISSUER=http://127.0.0.1:8080
OIDC_BACKCHANNEL_URL=http://127.0.0.1:8080
OIDC_CLIENT_ID=<Portal application client id>
OIDC_CLIENT_SECRET=<Portal application secret>
OIDC_REDIRECT_URI=http://127.0.0.1:9130/api/sso/callback
OIDC_POST_LOGIN_REDIRECT=/sso/callback
```

`OIDC_ISSUER` is used for browser redirects and
`OIDC_BACKCHANNEL_URL` is used by the Aegis process for Token, JWKS and
UserInfo requests. Direct Portal service launch uses:

```text
http://127.0.0.1:9130/?organization_id=<id>&client_id=<client_id>
```

The login page SSO button, shown only when `OIDC_CLIENT_ID` and
`OIDC_CLIENT_SECRET` are set, uses `/api/sso/start?sso=1`. Both paths use
Authorization Code + S256 PKCE and the registered callback
`/api/sso/callback`. Portal owns organization and subscription authorization;
`subscription_id` is not sent through the browser.

The callback places a short-lived, one-time HttpOnly ticket in a cookie and
redirects to `/sso/callback`. The frontend exchanges that ticket at
`POST /api/sso/exchange`, then stores the existing Aegis local JWT. Portal
roles are not mapped to Aegis administrator rights. New SSO users are enabled
regular users with a random unusable local password; administrators may reset
a password through the existing user-management API if local login is needed.

## Lark SSO

The login page supports Lark OAuth login when `LARK_SSO_ENABLE=true`. The
default is `false`; when disabled, the login option is hidden and
`/api/lark/start` rejects new login attempts. `GET /api/system/bootstrap`
reports `lark_sso_enabled` only when the flag is on and `LARK_APP_ID` /
`LARK_APP_SECRET` are set, and `oidc_sso_enabled` only when `OIDC_CLIENT_ID` /
`OIDC_CLIENT_SECRET` are set; the login page hides each SSO button whose start
route would answer 503. The response carries these booleans only, never the
configured values. Configure the Lark application
credentials in the process environment and register the exact callback URL in
the Lark developer console:

```dotenv
LARK_SSO_ENABLE=false
LARK_APP_ID=<Lark application App ID>
LARK_APP_SECRET=<Lark application App Secret>
LARK_REDIRECT_URI=http://127.0.0.1:9130/api/lark/callback
```

The Lark application must have the `contact:user.email:readonly` permission.
The browser starts at `/api/lark/start`, and the backend exchanges the
authorization code at Lark before requesting `/open-apis/authen/v1/user_info`.
Lark users are matched to Aegis accounts by normalized email. A missing email,
disabled local account, invalid state, or failed upstream request rejects the
login. The Lark access and refresh tokens are not stored; successful logins
reuse the existing Aegis SSO ticket and JWT exchange flow.

The existing `HERMES_HOME/aegis.db` is migrated additively at startup. The
`users` table gains nullable `oidc_subject` and `token_version` (default 0);
`oidc_login_transactions`, `sso_login_tickets` and `revoked_access_tokens` are
created if absent. Existing users, local passwords and already-issued tokens
are preserved.

To restart after frontend or backend changes:

```bash
hermes aegis --stop
hermes aegis --no-open
```

## Storage

The backend persists its state in:

```text
<HERMES_HOME>/a2a.json
```

The file-backed store uses this shape:

```json
{
  "a2a": {},
  "global": []
}
```

`a2a` stores agent definitions keyed by agent ID. `global` stores global
routing rules as an array. Agent URLs are persisted as normalized HTTP URLs;
legacy entries without a scheme such as `127.0.0.1:9086/a2a` are accepted and
served back as `http://127.0.0.1:9086/a2a`.

An entry that does not parse (for example a hand-added `"nopath":
"127.0.0.1:9086"`, which has neither a scheme nor a path) is skipped, with a
logged warning, from `/api/overview/agents`, `/api/overview/topology`, chat
shortcut resolution and the agent list. `GET /api/agents` reports it under
`invalid: [{"agent_id", "error"}]`; `GET /api/agents/{agent_id}` on it still
answers 500, and `PUT` / `DELETE /api/agents/{agent_id}` fix or remove it.

## API Surface In Scope

The authoritative list is `/openapi.json` (Swagger UI at `/docs`). Every
`/api/*` route requires a bearer token except the auth, SSO, Lark and bootstrap
routes marked public below. "Admin" routes additionally require an admin user.

- Auth (public): `POST /api/auth/login`, `POST /api/auth/register`,
  `GET /api/auth/session`, `POST /api/auth/logout`; `PUT /api/auth/password`
  (authenticated)
- Portal OIDC client (public): `GET /api/sso/start`, `GET /api/sso/callback`,
  `POST /api/sso/exchange`
- Lark SSO (public): `GET /api/lark/start`, `GET /api/lark/callback`
- System: `GET /health`, `GET /api/system/bootstrap` (public),
  `POST /api/system/restart` (admin)
- Users (admin): `GET|POST /api/users`, `PUT /api/users/{uid}/status`,
  `PUT /api/users/{uid}/role`, `PUT /api/users/{uid}/password`,
  `DELETE /api/users/{uid}`
- User roles (admin): `GET|POST /api/user-roles`,
  `PUT|DELETE /api/user-roles/{role_id}`
- RBAC rules (admin): `GET /api/rbac-rules`, `PUT /api/rbac-rules/{role}`,
  `POST /api/rbac-rules/test`
- Prompt templates (per user): `GET|POST /api/prompt-templates`,
  `PUT|DELETE /api/prompt-templates/{template_id}`. A tag names at most one of
  a user's templates (`@[prompt_<tag>]` resolves by tag): creating or renaming
  onto a tag already in use answers 409.
- System instructions (admin): `GET|POST /api/system-instructs`,
  `PUT|DELETE /api/system-instructs/{instruct_id}`
- User manuals: `GET /api/user-manuals`, `GET /api/user-manuals/{manual_id}`
- Per-user environment: `GET|PUT /api/my/user-env`,
  `DELETE /api/my/user-env/{env_key}`
- App Entry: `GET /api/app-entries` (Portal-backed organization application
  directory)
- A2A context: `GET /api/a2a/context`, `POST /api/a2a/context/refresh`
- Overview: `GET /api/overview/agents`, `GET /api/overview/stats`,
  `GET /api/overview/topology`
- Agents (admin): `GET /api/agents`, `GET|POST|PUT|DELETE /api/agents/{agent_id}`
- Routing (admin): `GET|POST /api/routing/global`,
  `GET|PUT|DELETE /api/routing/global/{rule_id}`, `GET|POST /api/routing/agent`,
  `GET|PUT|DELETE /api/routing/agent/{rank_id}`
- Audit (admin): `GET /api/audit/a2a-delegates`, `GET /api/audit/tasks`
- Chat: `POST /api/chat/attachments`, `GET /api/chat/quick-commands`,
  `GET /api/chat/drawer-html`, websocket `/api/chat/ws?token=<jwt>`
- Public skill JavaScript: `/static/skills/html-deliverable/assets/agent2ui-bridge.js`

The public skill JavaScript route only serves regular `.js` files located under
the repository's `aegis/skills/` directory. It does not list directories or
expose templates, documents, or other file types.

## Recent Chat Updates

The Aegis chat websocket endpoint at `/api/chat/ws` now includes a small set of
Aegis-native slash commands that are handled directly in the backend and return
through the normal `message.completed` event flow:

- `/help` — list the Aegis-native slash commands currently supported by the
  backend.
- `/model <model_name> [--provider <provider>]` — switch the current live
  session agent to a new model without recreating the session. Flags are parsed
  with the shared `/model` parser, so `--provider` selects the provider instead
  of becoming part of the model name; `--global`, `--once` and `--reasoning`
  are refused because the override is in-memory and session-scoped only. If the
  target provider matches the current one and the resolved switch result omits
  runtime credentials, Aegis preserves the live agent's existing `api_key`,
  `base_url`, and `api_mode` so the session does not lose authentication.
- `/a2a` — return the current `A2A_CONTEXT` XML. If the cache is empty, the
  backend refreshes it by calling `a2a_list()` first.

Agent creation in Aegis also now uses the `tools.a2a_delegate_tool.A2A_CONTEXT`
module global directly for the ephemeral system prompt. When the cache is
empty, Aegis refreshes it with `a2a_list()` and then injects the refreshed XML.

`message.send` optionally accepts an `args` JSON object. Aegis expands shortcut
tokens first, then substitutes every matching `{key}` in the resulting prompt
with the corresponding `args[key]` value. Variables without an `args` value are
left intact, including unresolved variables in Agent shortcuts. For example:

```json
{
  "type": "message.send",
  "text": "@[prompt_incident] Review {indicator}.",
  "args": {"indicator": "example.com"}
}
```

### Chat websocket contract

- Each frame must be a JSON object. A binary, non-JSON or non-object frame is
  answered with `{"type": "error", "code": "invalid_message"}` and the socket
  keeps serving. An `HTTPException` raised while resolving a `message.send`
  becomes an `error` event (`code: "message_rejected"`) carrying its detail.
  However the socket ends, the session actor is detached from it, so a dead
  socket never pins the actor against cache eviction.
- `session.bound.resumed` is true only when the session has prior state: the
  in-memory actor was bound before, or SessionDB holds turns for its runtime
  session. A new conversation whose id the client minted reports `false`.
- Work that can block — building the agent (which refreshes the A2A context and
  probes every agent card), `message.send` including `/model` and `/a2a`,
  approval and clarify responses, `session.interrupt` — runs in a worker
  thread, one frame at a time per socket, so a slow A2A agent never stalls the
  event loop. The A2A context, login and password-hashing HTTP handlers are
  plain `def` routes for the same reason. New actors are still built under the
  session manager's lock, so concurrent binds that each create a session wait
  for one another.

### Attachments and the drawer

`POST /api/chat/attachments` returns `{id, kind, media_type, display_name,
size}`; the browser references an upload by `id` in `message.send`, and the
host cache path stays server-side.

`GET /api/chat/drawer-html?path=…` previews only files that the requesting
user's own agent turns wrote (`write_file` / `patch` results recorded in
`HERMES_HOME/aegis_drawer_files.json`, newest 1000 per user) and that the core
read guard allows. Location grants nothing: the process cwd is `HERMES_HOME`,
which also holds `.env`, `config.yaml` and other users' data, and the agent's
file tools may write outside it (relative paths follow the terminal cwd). An
unowned file answers 404 exactly like a missing one; a protected file the user's
agent wrote answers 403. Relative paths resolve against `HERMES_HOME`.

Interactive docs are available at:

```text
http://127.0.0.1:9130/docs
```

The hosted frontend login page is available at:

```text
http://127.0.0.1:9130/login
```

`hermes aegis` automatically builds `aegis/frontend` when the source files are
newer than `aegis/backend/web_dist`. Use `--skip-build` only when the dist has
already been prepared.

## Manual Smoke Checks

After startup, do a quick sanity check:

- Set `AEGIS_BOOTSTRAP_ADMIN_PASSWORD` before the first startup, then sign in
  at `http://127.0.0.1:9130/login` with `admin` and that chosen password.
- Check `GET /health` returns `{"status":"ok"}`.
- Open `http://127.0.0.1:9130/docs` and confirm Swagger UI loads and exposes
  bearer auth through the `Authorize` button.
- Call a protected endpoint such as `GET /api/agents` without an
  `Authorization` header and confirm it returns `401`.
