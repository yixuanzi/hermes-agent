# Aegis fork: published plugin API additions

Bundled plugins may import only the names in `hermes_cli/data/plugin_api.json`
(`scripts/boundary_lint.py`, `hermes plugins validate`). The fork's plugins
(`plugins/ext-tools`, `plugins/rbac-guard`, `plugins/aegis_a2a`, `plugins/aegis_products`,
`plugins/aegis_source`) use the two "Aegis fork" groups below on top of
upstream's list and the `ctx` surface (`ctx.session_surface()`, `ctx.session_id()`, ...).
Every name is listed because no `ctx` method or upstream-published name covers the need.

## Plugin-facing surfaces of the fork's own core modules

| Name | What a plugin uses it for |
|---|---|
| `tools.user_env_runtime:get_current_user_env_identity` | The platform user a tool call or plugin command acts for (bound by the tool executor and the gateway); `None` when there is none. Never taken from model arguments. |
| `agent.tool_call_agent:current_tool_call_agent` | The live `AIAgent` whose tool call is running on this thread (bound by the tool executor on every path); `None` outside a tool call and in a host-isolated plugin process. For a tool that must act on the running agent itself — `aegis_a2a_delegate` reads its gateway-bound foreground I/O, caller identity and activity heartbeat, and parks its cancel handle on it. The registry passes handlers ids only. |
| `aegis_common.source_header:format_source_header` | Writes the Aegis `<source>{...}</source>` identity header from the fields a caller picks (order kept). Every value is collapsed to one inert line and `<`/`>` inside the JSON are escaped, so a display name cannot break the header. Used by `aegis_source` (gateway turns), `aegis_a2a` (remote envelope) and the Aegis console chat. |
| `tools.user_env_store` (module) | The calling user's persisted env values: `list_user_env`, `set_user_env_var`, `delete_user_env_var`, `CURRENT_USER_NAME_KEY` (system-managed, never deletable). |
| `tools.cronjob_visibility:caller_identity` | Same identity as above, as the cron visibility rules see it. |
| `tools.cronjob_visibility:resolve_caller_job` | A cron job by ID or name, as the caller may see it (own and ownerless jobs only; another user's job reads as missing). Ambiguity raises `AmbiguousJobReference`, a `LookupError`, over the caller's own matches only. |
| `agent.codex_runtime:attach_codex_app_server_session` | Create or re-bind a codex app-server thread the plugin owns onto an `AIAgent` (Hermes never retires it, overrides its turns' model, or stores its thread as the session's resumable codex thread). |
| `agent.codex_runtime:owner_session_approval` | `(approval_callback, auto_approve)` for a non-codex owner session (e.g. Claude Code over ACP) with the policy Hermes' own codex sessions get. |
| `agent.codex_owner_runtime` (module) | `enter_owner_codex_runtime(agent)` / `leave_owner_codex_runtime(agent)`: route the agent's turns to its owner-attached session at a turn boundary while auxiliary routing keeps the host `api_mode`. |
| `agent.transports.codex_app_server:check_codex_binary` | Whether the configured codex CLI is usable, and its version. |
| `agent.transports.codex_app_server_session:CodexAppServerSession`, `TurnResult` | The owner-session type and the turn result shape an owner backend returns. |
| `agent.transports.codex_event_projector:ProjectionLimits` | Per-thread clamps on what an owner thread writes to Hermes history. |

## Generic helpers the fork's bundled plugins need

| Name | Why |
|---|---|
| `hermes_cli.process_identity:spawn_owned`, `release_process_group_once_empty` | Start a long-lived child that dies with Hermes (owner-death supervisor), and hand its group back once stopped. The one sanctioned way to own a child (`scripts/check` detached-spawns). |
| `tools.environments.local:hermes_subprocess_env` | The scrubbed environment for a child process (`inherit_credentials=False` keeps Hermes' provider keys out of it). |
| `tools.approval:is_approval_bypass_active`, `is_approval_bypass_active_for_session` | Whether an explicit approval bypass (`approvals.mode: off`, `/yolo`) is on, to show users what will be auto-approved. |
| `gateway.session:neutralize_untrusted_inline_text` | Neutralize untrusted inline metadata before it reaches a model prompt. |
| `hermes_platform.resolver:locate_command` | Resolve an executable (e.g. `lark-cli`) the way the rest of Hermes does; AGENTS.md routes every executable lookup through `hermes_platform`. Used by the Feishu group-history CLI backend and `lark_user`. |
| `hermes_time:get_timezone` | The configured user timezone (`None` = local), for plugins that render timestamps (Feishu group-history `MM-DD HH:MM` stamps). |
