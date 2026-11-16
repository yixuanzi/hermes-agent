---
name: feishu-openid-to-userid
description: "Resolve a Feishu/Lark tenant User ID from a known Open ID without interacting with the target user. Use whenever a task needs to map an `ou_...` Open ID from an event, mention, or message into the tenant-scoped `user_id`, validate Feishu contact-directory permissions, or diagnose a missing user_id field."
version: 1.1.0
---

# Feishu Open ID → User ID

Use this skill to resolve a tenant-scoped Feishu/Lark `user_id` from a known app-scoped `open_id`, without initiating any interaction with the target user.

## Preconditions

The lookup operates with the Feishu app's `tenant_access_token`, so the target user must be inside the app's configured contact-directory data scope. The application must also have the self-built-app field permission **获取用户 user ID**; otherwise the user lookup can succeed but omit the `user_id` field.

The default credential file is derived from `$HERMES_HOME`:

```text
$HERMES_HOME/.env
```

This selects the active Hermes profile's credentials. When `HERMES_HOME` is not set (for standalone use), the script falls back to `~/.hermes/.env`. Use `--env-file` to select a different credential file explicitly.

Expected variables:

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_DOMAIN=lark|feishu|https://<custom-open-platform-domain>
```

Never display the app secret, tenant access token, or full credential-file contents.

## Run the bundled script

```bash
python3 scripts/feishu_openid_to_userid.py \
  --open-id 'ou_af0112b058cfff3f2da9feb5ca819c35'
```

For a non-default profile or credential file:

```bash
python3 scripts/feishu_openid_to_userid.py \
  --open-id '<open_id>' \
  --env-file '/path/to/.env'
```

Use `--format json` in automation. The script retrieves a `tenant_access_token`, calls Contact v3 `GET /users/{open_id}?user_id_type=open_id`, and emits only non-secret response fields.

## Interpret outcomes

| Exit code | Meaning | Action |
|---:|---|---|
| 0 | `user_id` returned | Use the returned tenant-scoped ID. |
| 2 | Configuration, token, HTTP, or API lookup error | Verify app credentials, open-platform domain, and the target's contact data scope. |
| 3 | User matched but `user_id` omitted | Enable and publish **获取用户 user ID**, then retry. Do not infer an ID from the Open ID. |

## Operational boundaries

- `open_id` is app-scoped and differs across applications; `user_id` is stable only inside one tenant.
- This works for users visible in the app's contact-directory scope; it does not bypass tenant visibility restrictions or guarantee results for external collaborators.
- Return the resolved `user_id`, display name (when returned), and a concise error/remediation message. Do not report fabricated IDs.