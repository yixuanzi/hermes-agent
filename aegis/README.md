# Aegis

`aegis/` is a standalone Hermes web application served by `hermes aegis` on
`http://127.0.0.1:9130` (the fork's sibling consoles are `hermes aisoc` and
`hermes workagent`). Deployment variables for all three are listed in
[`aegis/.env.example`](.env.example).

## Aegis Web Portal SSO

Aegis can act as an OIDC client of the Aegis Portal:

- Portal service launch enters Aegis with `organization_id` and `client_id`.
- The Aegis login page starts SSO with `client_id` and `sso=1`.
- Portal handles login and multi-organization selection.
- Aegis creates or binds a local non-admin user by OIDC subject/email.
- `subscription_id` never enters the browser OIDC flow.

Configure `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` only in the runtime
environment. The callback is `http://127.0.0.1:9130/api/sso/callback`; the
one-time local ticket is exchanged at `/api/sso/exchange` before the existing
Aegis JWT is written to browser storage.

Restart the standalone service with:

```bash
hermes aegis --stop
hermes aegis --no-open
```

See [`backend/README.md`](backend/README.md) and
[`frontend/README.md`](frontend/README.md) for configuration and verification
details.
