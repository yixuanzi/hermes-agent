---
name: feishu-cloud-document
description: "Use when managing/read/writing/create Feishu/Lark cloud docs and permissions."
version: 1.1.0
platforms: [linux, macos]
---

# Feishu / Lark 云文档操作（feishu-cloud-document）

凭一个 Feishu/Lark 云文档 URL（或裸 token）读取其内容，无需 Hermes 依赖。支持：

- `docx` 新版文档 → `raw_content` 直读
- `sheets` 电子表格 → 每个子表渲染为 Markdown 表格（默认前 500 行）
- `wiki` 知识库节点 → 自动 `get_node` 解析真实 doc 再读

**全生命周期操作**（创建 / 写入 / 删除 / 权限管理 / 所有者让渡）由同目录的
`scripts/feishu_doc_ops.py` 提供，见下方「文档写入与管理（feishu_doc_ops.py）」。

旧版 `docs`/`doc` 无 `raw_content` 接口，需先在飞书中转换为新版文档。

## When to Use / 何时激活

- 用户粘贴或提供 `*.feishu.cn` / `*.larksuite.com` 的 `docx`、`sheets`、`wiki` 链接
- 用户要求「读取 / 总结 / 提取 / 归档」某份飞书表格或文档的内容
- 需要把飞书文档内容拉进本地做后续处理（归档到 Wiki、分析等）
- 用户要求「新建 / 创建」飞书文档并写入内容
- 用户要求「写入 / 追加 / 更新」某份飞书文档的内容
- 用户要求「删除 / 清理」某份飞书文档
- 用户要求给自己或他人「加权限 / 改权限 / 移除权限 / 查看谁有权限」
- 用户要求「让渡 / 转移文档所有者」（管理员权限让渡）
- 用户要求查看或修改文档的「分享设置 / 链接权限」

不确定用户意图（只是读取 vs. 归档 vs. 分析）时，先用 clarify 问清楚再动手。

## 前提：凭证

脚本从环境变量读取凭证（运行时动态生效）：

- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`（必填，除非提供 user token；缺失时脚本显式报错，不做推断）
- `FEISHU_DOMAIN`：`feishu` | `lark`（**必填**；缺失或值非法时脚本显式报错，不做默认推断）
- `FEISHU_USER_ACCESS_TOKEN`（可选；以用户身份调用，可读「应用不可见但用户可见」的文档）

**凭证加载**：这些变量通常**不在**当前 shell 环境里。惯例位置是 `$HERMES_HOME/.env`，运行前先 source：

```bash
set -a && . $HERMES_HOME/.env 2>/dev/null && set +a
```

不确定当前生效的域名/凭证时，先跑 `--test-conn` 验证连通性再执行正式操作。**绝不读取 / 打印 / 复述任何密钥值**（红线），用 `set -a; . <env>; set +a` 一次性载入即可，不要 echo 变量值。若你的环境把凭证放在其他位置（CI、容器等），source 对应文件或注入等价环境变量。

## 用法

### 读取（feishu_doc_fetch.py — 只读）

**始终运行本技能自带的脚本** `scripts/feishu_doc_fetch.py` —— 它随技能分发、自包含，是唯一权威运行目标。用 `skill_view(name='feishu-cloud-document')` 返回的 `skill_dir` 拼出绝对路径 `<skill_dir>/scripts/feishu_doc_fetch.py`。

```bash
set -a && . $HERMES_HOME/.env 2>/dev/null && set +a && \
python3 <skill_dir>/scripts/feishu_doc_fetch.py "<完整URL>"
```

- URL 里的 `?sheet=<id>` 会被识别，只读该子表；不带则读全部子表。
- 内容默认打到 stdout（Markdown）。
- `--out FILE` 写入文件而非 stdout；`--json` 输出含 `token`/`obj_type` 的 JSON。
- 表格默认前 500 行；需要更多设 `FEISHU_SHEET_MAX_ROWS=<n>`。

### 文档写入与管理（feishu_doc_ops.py）

同目录 `scripts/feishu_doc_ops.py`，子命令式（复用相同环境变量鉴权）：

```bash
# 连通性
python3 <skill_dir>/scripts/feishu_doc_ops.py --test-conn

# 创建文档（可带初始内容，支持 #/##/### 标题、- 列表、普通段落）
python3 <skill_dir>/scripts/feishu_doc_ops.py create --title "标题" [--folder FOLDER_TOKEN] \
    [--content-text "..." | --content-file FILE]

# 写入已有文档（--mode append 追加 / overwrite 清空重写）
python3 <skill_dir>/scripts/feishu_doc_ops.py write <URL或token> --content-text "..." [--mode append]

# 读取（代理到 feishu_doc_fetch.py）
python3 <skill_dir>/scripts/feishu_doc_ops.py read <URL或token> [--json]

# 删除文档（移入回收站；必须 --yes 确认）
python3 <skill_dir>/scripts/feishu_doc_ops.py delete <URL或token> --yes

# 权限：添加 / 更新 / 移除 / 列出成员
python3 <skill_dir>/scripts/feishu_doc_ops.py perm add    <URL> --member <open_id> --role edit|view|full_access
python3 <skill_dir>/scripts/feishu_doc_ops.py perm update <URL> --member <open_id> --role edit|view|full_access
python3 <skill_dir>/scripts/feishu_doc_ops.py perm remove <URL> --member <open_id>
python3 <skill_dir>/scripts/feishu_doc_ops.py perm list   <URL>

# 权限：所有者让渡（操作者需为 owner 或具备管理权限）
python3 <skill_dir>/scripts/feishu_doc_ops.py perm transfer <URL> --to <open_id>

# 权限：公共设置查询/修改（无参数=查询）
python3 <skill_dir>/scripts/feishu_doc_ops.py perm public <URL> [--external true|false] [--entityedit true|false]
```

输出均为 JSON（`document_id`、`url`、`blocks_written` 等），便于编排时解析。

**手工调 API 或扩展脚本前，先查 `references/docx-api-pitfalls.md`** —— 实测验证过的 blocks 结构模板与权限 API 参数坑（heading 块型映射、DELETE 必带 member_type 等）。

## 常见错误与处置

- `需要环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET`：没 source `.env`。按上面的 `set -a; . <env>; set +a` 载入后重试。
- `未设置 FEISHU_DOMAIN`：脚本不做默认推断；确认 `.env` 中有 `FEISHU_DOMAIN=feishu|lark`。
- `获取表格元信息失败 ...`（权限类）：把文档分享给本应用/机器人，或配置 `FEISHU_USER_ACCESS_TOKEN`。
- `旧版文档(doc)无 raw_content 接口`：在飞书中「转换为新版文档」后重试。
- `无法从输入中解析出文档 token`：URL 不含可识别前缀（docx/sheets/wiki/...）或不是裸 token。
- `perm remove` 报 field validation failed：脚本已自动带 `member_type` 查询参数；若手工调 API 记得 DELETE 时也要带。
- **overwrite 清空旧内容（实测验证 2026-09）**：清空必须用 `DELETE /documents/{doc_id}/blocks/{doc_id}/children/batch_delete`，且 `start_index`/`end_index` 必须放 **JSON body**（放 query string 会报 99992402 field validation failed）。直接 `DELETE /blocks/{block_id}` 路由不存在（404 page not found）。
- `delete`（删除文档）报 `Access denied ... [drive:drive, space:document:delete]`：应用 token 缺少删除 scope，需在飞书开放平台开通 `space:document:delete` 或改用 user token；脚本会显式报错，不会静默失败。

## 读取之后（衔接下游）

读到内容后按用户意图继续：只需总结就直接整理输出；要归档到 Wiki 则走 Wiki 的 Raw → LLM Compile → Wiki 流程（音视频/图片类不做知识化编译）。

## Pitfalls

- **绝不读/打印/转发任何密钥**：只 source `.env`，不要读取或 echo 其中的值。这是 Aegis 红线。
- **多行 heredoc 执行 Python 会触发防护并超时**：不要 `python3 << 'EOF'`。脚本已是文件，直接 `python3 <file>` 单行运行。
- **不要把凭证写进命令行参数**：脚本只从环境变量取值，命令行不该出现任何 token/secret。
- **只跑技能内脚本**：运行目标恒为 `<skill_dir>/scripts/` 下的 `feishu_doc_fetch.py`（读取）和 `feishu_doc_ops.py`（写/管）。
- **不写环境快照式断言**（如「本机当前为 lark」）：环境会变；改为「用 `--test-conn` 动态发现当前值」。
