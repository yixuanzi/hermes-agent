# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read these first

- **`AGENTS.md`** (repo root, ~1500 lines) — the canonical development guide: contribution rubric, footprint ladder, agent loop, CLI/TUI/desktop architecture, plugins, skills, toolsets, delegation, cron, kanban, policies, pitfalls, testing rules. Treat it as authoritative; everything below is a map and a delta, not a replacement.
- **`MEMORY.md`** (repo root) — fork-local project rules, including the **Aegis change-intent summary gate**: any task that changes non-test code *outside* `aisoc/`, `workagent/`, `aegis/` must update the per-file `Feature`/`Intent` block in `hermes_change_log_by_aegis.md` (edit the existing pair in place; don't append task logs). That rule intentionally lives only in `MEMORY.md` — read it there rather than assuming the details.
- **`apps/desktop/AGENTS.md`** — scoped rules for the Electron desktop app.
- `CONTRIBUTING.md` for dev setup; `gateway/platforms/ADDING_A_PLATFORM.md` before touching a messaging platform.

This checkout is a **fork** (`origin` = `yixuanzi/hermes-agent`) of NousResearch/hermes-agent, living in the managed install layout at `$HERMES_HOME/hermes-agent`. So `~/.hermes` is simultaneously the runtime home (config, sessions, logs, skills) and the parent of this source tree — be careful with relative paths that could hit runtime state.

## Commands

```bash
source .venv/bin/activate        # or venv/ — run_tests.sh probes .venv, venv, then ~/.hermes/hermes-agent/venv
uv pip install -e ".[all,dev]"   # dev/test extras
```

Python tests — **always via the wrapper, never bare `pytest`** (it enforces CI parity: blanked credential env, `TZ=UTC`, `LANG=C.UTF-8`, temp `HERMES_HOME`, one subprocess per test file):

```bash
scripts/run_tests.sh                                      # full suite (~17k tests)
scripts/run_tests.sh tests/gateway/                        # one directory
scripts/run_tests.sh tests/gateway/test_feishu_card_output.py -k test_x   # runner is file-granular; narrow with -k
scripts/run_tests.sh -j 4 -v --tb=long                     # bare pytest flags pass through
```

A failing file is auto-retried once; a pass-on-retry shows up in the `⚠ FLAKY` summary and is a bug to fix, not noise. Integration tests are excluded by default (`-m 'not integration'`).

Lint / typecheck (what CI blocks on):

```bash
ruff check .        # only PLW1514 (explicit encoding) is enforced — it is not a general style gate
ty check            # advisory diff in CI
```

JavaScript/TypeScript (npm workspaces: `ui-tui`, `web`, `apps/*`, `tests-js`; install from the repo root):

```bash
npm run check                    # every workspace: typecheck + vitest + eslint
npm run check --workspace ui-tui # one workspace
npm run fix                      # eslint --fix + prettier across workspaces
cd apps/desktop && npx vitest run src/lib/desktop-slash-commands.test.ts   # single JS test
cd ui-tui && npm run dev         # TUI watch mode
cd apps/desktop && npm run dev   # Electron renderer + main
```

Running the agent: `hermes` (CLI), `hermes --tui`, `hermes gateway`, `hermes dashboard`, `hermes serve` (headless backend for the desktop app), `hermes doctor`, `hermes logs [--follow]`.

## Architecture

One agent core, many surfaces. The core is deliberately a narrow waist; capability belongs at the edges (plugins, skills, MCP, service-gated tools).

```
run_agent.py  AIAgent — the conversation loop (system prompt → LLM → tool dispatch → persist)
model_tools.py  tool orchestration: discover_builtin_tools(), get_tool_definitions(), handle_function_call()
toolsets.py     toolset definitions + _HERMES_CORE_TOOLS (every core tool ships in every request's schema)
hermes_state.py SessionDB — SQLite session store with FTS5 search
hermes_constants.py get_hermes_home() / display_hermes_home() — profile-aware paths
```

Import chain (one direction only): `tools/registry.py` ← `tools/*.py` (each self-registers at import) ← `model_tools.py` ← `run_agent.py` / `cli.py` / `batch_runner.py` / `tools/environments/`.

Surfaces, all driving the same `AIAgent`:

| Surface | Entry | Notes |
|---|---|---|
| Classic CLI | `cli.py` (`HermesCLI`), `hermes_cli/` | prompt_toolkit; slash registry in `hermes_cli/commands.py`; menus **must** use `hermes_cli/curses_ui.py` |
| TUI | `ui-tui/` (Ink/React) ↔ `tui_gateway/` (Python) | newline-delimited JSON-RPC over stdio; TS owns the screen, Python owns sessions/tools/commands |
| Messaging gateway | `gateway/run.py` (~28k LOC) + `gateway/session.py` + `gateway/platforms/` | one process, ~20 platforms; adapters are plugins under `plugins/platforms/` too |
| Desktop app | `apps/desktop/` (Electron) + `apps/shared` | its own transcript/composer over JSON-RPC to a `hermes serve` backend — **not** the embedded TUI |
| Dashboard | `hermes_cli/web_server.py` + `web/` | embeds the real `hermes --tui` through a PTY bridge; don't reimplement chat in React |
| Editors | `acp_adapter/` | ACP for VS Code / Zed / JetBrains |

Extension points, in increasing footprint: extend existing code → CLI command + skill (`skills/`, `optional-skills/`) → service-gated tool (`check_fn`) → plugin (`plugins/…`: memory, model-providers, context_engine, platforms, kanban, rbac-guard, …) → MCP server → new core tool. AGENTS.md calls this the Footprint Ladder; use the least-footprint rung that works.

State lives in `~/.hermes/`: `config.yaml` (settings), `.env` (**secrets only**), `sessions/`, `logs/`, `skills/`, `plugins/`. Profiles give each instance its own `HERMES_HOME`.

## Fork-specific subsystems

Three in-repo product apps not present upstream, each a FastAPI backend + Vite frontend + its own skills, launched as CLI subcommands wired in `hermes_cli/main.py` and tested under `tests/<name>/`:

- **`aegis/`** — security-AI hub / router agent (`hermes aegis`; `aegis/backend/main.py`). Design and integration docs live in `aegis/docs/` (mostly Chinese).
- **`aisoc/`** — SOC agent console with an A2A server and an `extcli` terminal client (`hermes aisoc --module server|a2a|extcli --port 9120`).
- **`workagent/`** — work-agent service; owns the current `hermes.interaction.v1` A2A approval/clarify implementation (`workagent/backend/docs/a2a-interaction-extension.md`).

All three bind `HERMES_HOME` (and `HOME` → `<HERMES_HOME>/home`) from the launching agent/profile at startup, so their data is profile-scoped rather than cwd-scoped; `hermes -p <profile> <app>` selects it.

Caller side of that story: `tools/a2a_delegate_tool.py` and `tools/a2a_delegate_aegis.py` (behind the opt-in `a2a` toolset), plus `plugins/rbac-guard/` for role gating. Delegate approval/clarify UI state is deliberately separate from the gateway's local approval state.

The Feishu/Lark adapter (`plugins/platforms/feishu/` — `adapter.py` ~7.6k LOC, `feishu_cardkit.py`, comment/meeting modules) carries most of the fork's active work; its tests are `tests/gateway/test_feishu_*.py`.

## Non-negotiables (details in AGENTS.md)

- **Never break per-conversation prompt caching.** No mutating past context, swapping toolsets, or rebuilding the system prompt mid-conversation; context compression is the only exception. Slash commands that change prompt state default to deferred invalidation with an opt-in `--now`.
- **Never hardcode `~/.hermes`.** `get_hermes_home()` for paths, `display_hermes_home()` for user-facing text — hardcoding breaks profiles.
- **Surface capability is a property of the session, not the process env.** Gate GUI-only tools through a named toolset resolved from the session's platform, not `HERMES_DESKTOP=1`; `check_fn` results are TTL-cached process-wide, so they can't answer per-session questions.
- **Tests never write to real `~/.hermes/`**, never read source files as text, never assert on data expected to change (model catalogs, config versions, enumeration counts), and never fake the host OS — use `@pytest.mark.{linux,macos,windows}_only`, not `skipif`.
- Tests asserting about `package.json`, lockfiles, or `.ts`/`.tsx` files belong in the vitest suites, not `tests/*.py` — the CI change classifier won't run Python jobs for JS-only PRs.
- Direct dependencies are exact-pinned in `pyproject.toml` (supply-chain policy); bump the pin and regenerate `uv.lock`.
- Tool schema descriptions must not name tools from other toolsets; add cross-references dynamically in `get_tool_definitions()`.
- The gateway has two message guards (base adapter `_pending_messages`, then `gateway/run.py` control-command interception) — a new control command that must reach a blocked agent has to bypass both, dispatched inline.
