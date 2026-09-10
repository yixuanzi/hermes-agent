# Cron job 启动时的 toolset 加载与 `enabled_toolsets` 语义

## 研究范围与结论

本文只描述当前代码（2026-09-10 工作树）中，计划任务从创建/持久化到执行，再到 `AIAgent` 工具 schema 解析的链路；没有修改业务代码。仓库中未发现既有 `docs/research/` convention，因此本笔记放在 `docs/research/cron-toolsets.md`。

核心结论：

1. `enabled_toolsets` 是 job 级的工具集 allowlist：有非空值时优先于 `platform_toolsets.cron`；没有值时使用 cron 平台的全局工具集配置。
2. 这个 allowlist 不是最终权限边界。每次 cron agent 启动时还会传入固定的 cron denylist（`cronjob`、`messaging`、`clarify`、`memory`）以及全局 `agent.disabled_toolsets`；后者在 schema 计算末尾做减法，因此 job 配置不能绕过它们。
3. 空列表在创建、更新和 dashboard 参数转换中都会变成 `None`，所以当前没有“显式启用零个 toolset”的持久化语义；清空 job 的 `enabled_toolsets` 等同于删除 job override，然后回退到 cron 平台配置。
4. 最终是否出现某个 tool schema 还要经过 toolset 名称解析、registry 中的 tool membership，以及每个工具的 `check_fn` 可用性检查。`enabled_toolsets` 只选择候选工具，不保证所有候选工具都可用。

## 端到端路径

```text
创建入口
  ├─ cronjob model tool / blueprint / dashboard
  ├─ hermes cron CLI（当前不暴露 enabled_toolsets 选项）
  └─ API server（创建/更新白名单也不暴露该字段）
        ↓
  cron.scheduler.create_job_with_scheduler_registration()
        ↓
  cron.jobs.create_job() → jobs.json
        ↓
触发入口
  ├─ gateway → InProcessCronScheduler → tick()
  ├─ dashboard/backend ticker → resolved provider → tick()
  └─ external provider webhook → fire_due()
        ↓
  run_one_job() → run_job()
        ↓
  _resolve_cron_enabled_toolsets(job, cfg)
  _resolve_cron_disabled_toolsets(cfg)
        ↓
  AIAgent(... enabled_toolsets=..., disabled_toolsets=...)
        ↓
  agent.agent_init.init_agent()
        ↓
  model_tools.get_tool_definitions()
    → resolve_toolset()/validate_toolset()
    → registry.get_definitions() / check_fn
```

这是执行结构的代码依据：gateway 启动时解析 cron provider 并启动 scheduler thread（`gateway/run.py:28036-28085`）；默认 provider 的 `start()` 循环调用 `cron.scheduler.tick()`（`cron/scheduler_provider.py:172-247`）；`tick()` 对 due jobs 做 `advance_next_runs()` 后提交到 worker（`cron/scheduler.py:4977-4986`, `5015-5069`）。外部 provider 的 `fire_due()` 通过 store CAS claim 后复用同一个 `run_one_job()`（`cron/scheduler_provider.py:101-123`），因此两种触发方式共享 agent/toolset 逻辑。

## 创建与持久化

### 1. `cronjob` 模型工具

- `tools/cronjob_tools.py:1075-1100` 定义统一 `cronjob()` API，包含 `enabled_toolsets` 参数。
- create 分支在 `tools/cronjob_tools.py:1107-1157` 做 prompt/script/context 等校验；随后在 `tools/cronjob_tools.py:1160-1193` 调用 `create_job_with_scheduler_registration()`，把 `enabled_toolsets` 原样作为 job 参数传下去。
- tool schema 在 `tools/cronjob_tools.py:1591-1595` 声明它是一个字符串数组 allowlist，并说明空数组 update 用于清空。
- registry handler 在 `tools/cronjob_tools.py:1635-1666` 从模型参数读取 `args.get("enabled_toolsets")`；但同一个 handler 刻意不读取模型提供的 `model/provider/base_url`（`1650-1654`），toolset 则没有类似的模型禁止逻辑。

### 2. CLI `hermes cron create/edit`

`hermes_cli/cron.py:344-389` 将 CLI create 转成 `_cron_api(action="create", ...)`，调用模型工具 wrapper；parser 只定义到 `--provider` 等选项（`hermes_cli/subcommands/cron.py:27-107`），没有 `--enabled-toolsets`。edit 也只传 skills、脚本、模型等字段（`hermes_cli/cron.py:392-461`），没有传该字段。因此 CLI 可以管理已有 job，但当前不能通过标准 CLI 参数设置 job 级 `enabled_toolsets`。

### 3. Dashboard / Blueprint

- Dashboard 的 `CronJobCreate` 明确包含 `enabled_toolsets: Optional[List[str]]`（`hermes_cli/web_models.py:373-387`）。创建 worker 将其规范化后传入 `create_job`（`hermes_cli/web_server.py:12038-12068`）；`_cron_string_list()` 会去掉空项，空结果返回 `None`（`hermes_cli/web_server.py:11714-11724`）。更新也对该字段做同样转换（`hermes_cli/web_server.py:11771-11801`）。
- Blueprint 的 frontmatter 解析接受 `metadata.hermes.blueprint.enabled_toolsets` 数组（`tools/blueprints.py:95-141`）；`BlueprintSpec` 保存它（`tools/blueprints.py:57-69`），并在 `blueprint_to_job_spec()` 传给普通 `create_job`（`tools/blueprints.py:175-194`）。所以 blueprint 没有自己的 toolset 解析规则，只是 job 创建参数的另一种来源。

### 4. API server 的差异

`gateway/platforms/api_server.py` 的独立 `/api/jobs` create 只从 request body 读取 name/schedule/prompt/deliver/skills/repeat（`gateway/platforms/api_server.py:5593-5642`），没有把 `enabled_toolsets` 放进 kwargs；update 的 `_UPDATE_ALLOWED_FIELDS` 也只有 `name/schedule/prompt/deliver/skills/skill/repeat/enabled`（`gateway/platforms/api_server.py:5551`, `5667-5701`）。因此这个 API surface 当前不支持设置或更新 job 级 toolset。不要把它与 Dashboard 的 `/api/cron/jobs` route 混淆：后者支持该字段，见上一节。

### 5. `cron.jobs.create_job()` 的存储语义

`cron/jobs.py:1619-1669` 定义参数和 docstring；其中 docstring 描述 `enabled_toolsets` 是减少 token overhead 的限制项。实际归一化在 `cron/jobs.py:1718-1726`：每个值转成 stripped string，空项被过滤，整个空结果变成 `None`。job record 在 `cron/jobs.py:1789-1833` 固定写入 `"enabled_toolsets": normalized_toolsets`，随后在 `1840-1843` 加锁加载、追加并保存 `jobs.json`。

更新路径 `cron/jobs.py:1911-1947` 先合并原 job 与 updates；模型工具的 update 分支在 `tools/cronjob_tools.py:1352-1445` 中把 `enabled_toolsets` 设为 `enabled_toolsets or None`。这解释了空数组的关键行为：它不是一个空 allowlist，而是清除 override。Dashboard 更新也通过 `_cron_string_list()` 得到 `None`。

另一个边界是：`create_job()` 对 list 元素只做 strip，不做 toolset 名称验证或去重；update 的核心函数也不会在这里验证名称。未知名称是在后面的 toolset 解析阶段被忽略，而不是创建时拒绝。

## 执行前的 cron 特殊处理

### 调度与 agent run 的短路

`cron/scheduler.py:4595-4612` 说明 `run_one_job()` 只负责一次完整 fire，不负责 due 判断；它先做 one-shot dispatch claim 并标记 execution running（`4617-4638`），然后建立 profile secret scope（`4640-4655`），调用共享的 `run_job()`（`4663-4668`），最后保存输出、delivery、状态和 execution ledger（`4681-4804`）。

`run_job()` 在 `cron/scheduler.py:3213-3236` 执行单 job。两类 job 不会启动 `AIAgent`：

- `no_agent=True` 在 `cron/scheduler.py:3245-3363` 直接运行脚本，`enabled_toolsets` 没有作用。
- monitor job 如果 source 未变化，在 `cron/scheduler.py:3365-3410` 直接返回 silent，agent 不会构造；只有变化才进入正常 agent 路径。

正常路径先构造 prompt（包括 skill 内容、脚本输出、`context_from` 等），见 `cron/scheduler.py:3420-3551`。这里的 skill loading 是 prompt 组装，不是 toolset loading：附加 skill 决定注入哪些指令文本；`enabled_toolsets` 决定 agent 的工具 schema。

### MCP 发现与配置刷新

每次正常 cron run 会重新读取 profile 的 `.env` / `config.yaml`（`cron/scheduler.py:3711-3728`），在构造 agent 前显式调用 `discover_mcp_tools()`（`cron/scheduler.py:4112-4131`）。这是必要的，因为 MCP tool 会先注册到全局 registry，后续 allowlist 才能解析到对应的 MCP toolset。

### `AIAgent` 调用点

`cron/scheduler.py:4133-4168` 是 cron agent 的唯一关键构造点。它传入：

- `enabled_toolsets=_resolve_cron_enabled_toolsets(job, _cfg)`（`4152`）；
- `disabled_toolsets=_resolve_cron_disabled_toolsets(_cfg)`（`4153`）；
- `quiet_mode=True`（`4154`）；
- `platform=... or "cron"`、cron session id、session DB（`4163-4167`）；
- `skip_memory=True`、`skip_background_review=True`（`4161-4162`）。

`no_agent` 不会到达这里，因此也不会有 MCP discovery、toolset 解析或模型调用。

## `enabled_toolsets` 的解析规则

### Cron resolver 的优先级

`cron/scheduler.py:223-253` 的 `_resolve_cron_enabled_toolsets()` 是 cron job allowlist 的单一入口：

1. 如果 `job.get("enabled_toolsets")` truthy，使用该 per-job list，并先调用 `_merge_mcp_into_per_job_toolsets()`（`242-244`）。
2. 如果 job 没有非空 list，则调用 `_get_platform_tools(cfg, "cron")`，使用 `platform_toolsets.cron` 及其默认/插件/MCP 规则（`245-247`）。
3. 如果这个 lookup 抛异常，记录 warning 并返回 `None`（`248-253`）。`None` 进入 `model_tools` 后意味着“从所有 toolset 开始”，这是旧行为的 fallback；但正常配置下 cron 会使用平台 resolver，而不是简单地无条件加载全部工具。

注意这里使用的是 truthiness，而不是 `is not None`。配合创建/更新阶段把空数组归一为 `None`，当前无法让 job 通过 `[]` 选择零 toolset。

### Per-job list 如何处理 MCP

`_merge_mcp_into_per_job_toolsets()` 在 `cron/scheduler.py:192-220` 定义了额外层：

- list 含 `no_mcp`：删除 sentinel，不加入任何 MCP server；
- list 已经点名一个或多个启用的 MCP server：把这些 server 名视为 MCP allowlist，不自动加入其它 server；
- list 没有点名 MCP server：将 config / plugin 识别出的全部启用 MCP server union 进 job list。

这样可以保持 per-job native toolset allowlist，同时避免 MCP 已注册但因未被包含在 `enabled_toolsets` 而变成 “Unknown tool”。全球启用的 MCP 名称由 `hermes_cli/tools_config.py:2154-2188` 计算：配置项没有 `enabled` 时视为启用，显式 false-like 值才禁用，并包含 portable plugin MCP。

### Cron denylist 永远叠加

`_resolve_cron_disabled_toolsets()` 在 `cron/scheduler.py:167-189` 固定加入：

- `cronjob`：避免 cron agent 再创建 cron；
- `messaging`：需要 live gateway session；
- `clarify`：会等待用户输入；
- `memory`：cron agent 用 `skip_memory=True`，避免暴露没有后端 store 的 tool；
- config 的 `agent.disabled_toolsets` 中其它用户禁用项。

因此即使 job 存储了 `enabled_toolsets=["memory", "file"]`，AIAgent 仍同时收到 `disabled_toolsets` 中的 `memory`。已有回归测试直接验证这一点：`tests/cron/test_scheduler.py:557-577` 要求 `skip_memory=True`、job allowlist 保留原值、且 `memory` 出现在 denylist。

`hermes_cli.tools_config._get_platform_tools()` 同样在解析 cron 平台配置末尾减去全局 `agent.disabled_toolsets`（`hermes_cli/tools_config.py:2533-2542`）；cron agent 再传入 denylist 是防止 per-job allowlist 绕过该策略的第二道保证。

## 从 AIAgent 到最终 tool schema

### `AIAgent` / `agent_init`

`run_agent.py:435-512` 的构造函数接收 `enabled_toolsets` 和 `disabled_toolsets`，并在 `521-535` 转发给 `agent.agent_init.init_agent()`。`init_agent()` 把两者保存为 agent 的 session 属性（`agent/agent_init.py:852-854`），然后在 `agent/agent_init.py:1444-1456` 调用 `_ra().get_tool_definitions()` 生成本次 agent 的工具 schema；同时建立 `valid_tool_names`（`1458-1464`）。这意味着 schema 在 agent 初始化时确定，job 的 toolset 配置不会在对话中途重建系统 prompt。

### `model_tools.get_tool_definitions()`

`model_tools.py:305-328` 的契约是：

- `enabled_toolsets is not None`：只纳入指定 toolsets；
- `enabled_toolsets is None`：从全部 toolsets 开始；
- `disabled_toolsets`：在末尾做减法。

实现见 `model_tools.py:391-438`：对每个 enabled 名称先 `validate_toolset()`，再 `resolve_toolset()`；未知名称只在非 quiet 模式打印 warning（`415-427`）。当 `enabled_toolsets=None` 时遍历 `get_all_toolsets()`（`428-432`）。disabled 逻辑在 `434-475`，且对 `hermes-*` bundle / posture toolset 使用“只减非 core delta”的特殊处理（`441-464`）。

toolset 的递归/别名解析由 `toolsets.py` 完成：`resolve_toolset()` 在 `767-846` 递归展开 `includes`，支持 `all` / `*` 特殊别名（`790-798`）；`validate_toolset()` 接受内置 toolset、plugin toolset、registry alias 和 `all/*`（`toolsets.py:942-959`）。插件/MCP toolset 可以来自 live registry；`get_toolset()` 的 registry 分支见 `toolsets.py:666-736`。

### Registry 可用性过滤

`model_tools.py:483-489` 将候选 tool name 交给 `registry.get_definitions()`。registry 的实现只返回没有 `check_fn` 或 `check_fn()` 为 true 的工具（`tools/registry.py:717-764`），并对 check 结果做约 30 秒 TTL cache（`720-725`）。所以例如凭据缺失、运行时后端不可用的工具，即使其 toolset 在 job allowlist 中，也不会出现在最终 schema。

## 语义矩阵

| job 的 `enabled_toolsets` | cron resolver 结果 | 最终含义 |
|---|---|---|
| key 缺失 / `null` | `_get_platform_tools(cfg, "cron")` | 使用 cron 平台全局配置；其后叠加 cron denylist 与 registry `check_fn` |
| `[]` | 创建/更新时通常归一为 `None`，然后同上 | 清除 per-job override，不是零工具；这是当前最容易误解的行为 |
| `["web", "terminal"]` | 原 list +（通常）全部全局启用 MCP server | 只请求这些 native toolset 与 MCP 层；最后仍受 denylist / check_fn 影响 |
| 含已启用 MCP 名称 | 原 list 不再自动加入其它 MCP | MCP server 名称本身也作为 toolset 参与解析 |
| 含 `"no_mcp"` | sentinel 去除且不加入 MCP | 显式关闭该 job 的 MCP tools |
| 含未知 toolset 名 | 仍可持久化 | `validate_toolset()` 失败后被静默跳过（quiet cron agent 下不会打印）；可能得到较少甚至零个工具 |
| `no_agent=True` | 不进入 resolver | 只执行脚本，`enabled_toolsets` 无效 |

## 维护/排查要点

1. 看到 cron job “工具不见了”时，按顺序检查：job record 的 `enabled_toolsets` 是否为 null、`config.yaml` 的 `platform_toolsets.cron`、`agent.disabled_toolsets`、cron 固定 denylist、MCP 是否在 agent 构造前 discovery、最后是对应工具的 `check_fn`。
2. 不要把 job 的 `skills` 与 toolsets 混为一谈：skills 在 `_build_job_prompt()` 中加载文本（`cron/scheduler.py:2638-2704` 及后续 context 注入），toolsets 在 agent init 时筛 schema；一个不会自动替代另一个。
3. 不同 API surface 的能力不一致：`cronjob` tool、Dashboard cron route 和 Blueprint 可以携带 `enabled_toolsets`；标准 `hermes cron` CLI 与独立 `gateway/platforms/api_server.py:/api/jobs` 当前不能通过公开参数设置它。
4. 若未来要支持显式空 allowlist，需要同时改变存储归一化（`cron/jobs.py:1724-1725`）、resolver 的 truthiness 判断（`cron/scheduler.py:242-247`）、Dashboard/工具 wrapper 的空值转换，并补充“零工具但仍可启动”的测试；否则只改其中一层会把 `[]` 和未设置继续混淆。

## 主要源码索引

- Job 创建/存储：`cron/jobs.py:1619-1845`
- Job 更新：`cron/jobs.py:1911-2030`
- Cron 工具入口与 schema：`tools/cronjob_tools.py:1075-1201`, `1591-1668`
- Dashboard create/update：`hermes_cli/web_models.py:373-392`, `hermes_cli/web_server.py:11714-11801`, `12038-12112`
- Blueprint bridge：`tools/blueprints.py:57-69`, `95-141`, `175-214`
- 调度/执行：`gateway/run.py:28036-28085`, `cron/scheduler_provider.py:101-123`, `172-247`, `cron/scheduler.py:4595-4804`, `4902-5145`
- Cron toolset resolver：`cron/scheduler.py:167-253`
- Platform toolset resolver：`hermes_cli/tools_config.py:2154-2188`, `2273-2568`
- Agent 初始化：`run_agent.py:435-535`, `agent/agent_init.py:852-854`, `1444-1471`
- Tool schema filtering：`model_tools.py:305-500`, `toolsets.py:666-846`, `942-959`, `tools/registry.py:717-764`
- 现有语义测试：`tests/cron/test_scheduler.py:17-63`, `557-577`; `tests/cron/test_jobs.py:897-900`

## 计划任务与 `MEMORY.md` / `USER.md`

cron 在 `cron/scheduler.py:4133-4168` 构造 `AIAgent` 时传入
`skip_memory=True`，并把 `memory` 放入固定的
`disabled_toolsets`。这会让 memory tool 不进入最终 tool schema，但不必然阻止
本地持久化记忆进入 system prompt。

`agent/agent_init.py:1694-1722` 的实际条件是：当 `skip_memory` 为 false，或
原始 `enabled_toolsets` 显式/间接包含 `memory` 时，读取 `memory` 配置；只要
`memory_enabled` 或 `user_profile_enabled` 为 true，就创建 `MemoryStore` 并从
`~/.hermes/memories/MEMORY.md` / `USER.md` 加载快照。`agent/system_prompt.py:523-532`
随后把相应快照追加到 system prompt。因此，当前默认 cron platform toolset 通常
包含 `memory` 这个候选名时，且配置启用了 built-in memory/profile，cron 模型能看见
本地记忆内容，但不能调用 memory tool；这是“只读上下文”，不是 tool access。

反过来，如果 job 的 `enabled_toolsets` 明确给出不含 `memory` 的候选列表，或
`memory_enabled` / `user_profile_enabled` 均为 false，则 `skip_memory=True` 不会
创建 store，也不会注入这两份文件。外部 memory provider 始终在
`agent/agent_init.py:1726-1795` 的 `not skip_memory` 分支中初始化，所以 cron 不会
加载外部 provider 的记忆上下文。

相关回归测试：`tests/agent/test_skip_memory_store_65429.py` 验证
`skip_memory=True + enabled_toolsets=["memory"]` 仍会创建 built-in store，而
`tests/cron/test_scheduler.py:536-577` 验证 cron 的 memory tool schema 仍被固定
denylist 移除；两组共 70 个测试通过。
