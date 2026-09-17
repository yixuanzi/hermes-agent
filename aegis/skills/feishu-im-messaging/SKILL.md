---
name: feishu-im-messaging
description: Use when user wants to bring in chat/group history for Feishu/Lark. Read chat & group message history via IM API; defaults to latest 20 messages incl. thread replies.
---

# Feishu/Lark IM 消息（群历史消息）读取

读取飞书/Lark 会话（单聊/群聊/话题）历史消息的已验证工作流。触发场景：**用户要求"带入历史消息"**
（如"结合历史消息分析/汇总/回答"、"读取本群聊天记录"等）时必须加载本 skill；以及列出机器人所在群、
解析消息体、排查 IM API 错误（230002/230027 等）。

## 默认拉取策略（带入历史消息场景）

当用户要求带入历史消息但**未明确数量和范围**时，自动执行：

1. **读取当前 group 内最新 20 条消息**：`--chat <当前chat_id> --desc --limit 20`；
2. **若当前对话位于话题（thread）中**：将话题内消息也一并带入——用根消息的
   `thread_id`（`omt_` 前缀）执行 `--thread <thread_id>` 拉取全部回复（thread 无服务端
   时间/条数过滤，全量拉取后按需本地截断）；
3. 拉取后按时间升序整理再进入分析/汇总流程。

## 核心工具（已实测可用）

**本 skill 自带脚本 `scripts/feishu_chat_history.py`（唯一工具入口，自包含无外部依赖）**，
读取凭证复用 `$HERMES_HOME/.env` 中的
`FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_DOMAIN`（lark 域对应 open.larksuite.com，
feishu 域对应 open.feishu.cn）。用法（以 skill 目录为 `SKILL_DIR`）：

```bash
set -a; source $HERMES_HOME/.env; set +a
SKILL_DIR=$HERMES_HOME/skills/aegis/feishu-im-messaging
python3 $SKILL_DIR/scripts/feishu_chat_history.py --list-chats            # 列出机器人所在群，找 chat_id
python3 $SKILL_DIR/scripts/feishu_chat_history.py --chat oc_xxx           # 全量历史（自动分页）
python3 $SKILL_DIR/scripts/feishu_chat_history.py --chat oc_xxx --last-hours 240 --desc  # 时间范围+降序
python3 $SKILL_DIR/scripts/feishu_chat_history.py --thread om_xxx --raw   # 话题消息 / 完整 JSON
```

## 工作流

1. **确定容器 ID**：先用 `--list-chats`（底层 `GET /open-apis/im/v1/chats`）拿到 chat_id。
2. **拉取消息**：`GET /open-apis/im/v1/messages?container_id_type=chat&container_id=oc_xxx`，
   分页参数 `page_size`（1~50）+ `page_token`；时间过滤用秒级时间戳 `start_time`/`end_time`；
   排序 `sort_type=ByCreateTimeAsc|Desc`（翻页中途不可换排序）。
3. **解析消息**：见下方"关键坑"。
4. **汇总输出**：按日期/分类聚合；富文本(post)需展开 `content[].[].text` 段。

## 关键坑（2026-09 实测）

1. **响应 content 在 `body.content`，不是顶层 `content`**（新版 API 结构）。解析时
   `(m.get("body") or {}).get("content")` 兜底 `m.get("content")`。
2. **`create_time` 是 18 位「秒×1000+毫秒」拼接格式**（也可能是 13 位毫秒）：
   前 10 位是秒级时间戳，直接 `int(ts[:10])` 转换；两种长度都要兼容。
3. **群组消息权限**：接口默认只能读单聊(p2p)；读群组消息需应用开通「获取群组中所有消息」权限，
   且机器人必须在群内。错误码：`230002`（机器人不在群）、`230027`（缺权限）、
   `230006`（机器人能力未启用）。
4. **thread 容器不支持 start_time/end_time 时间过滤**（官方限制），且**实测为静默忽略**——
   传了不报错也不过滤，早于 start_time 的消息照常返回，勿被"成功"假象误导；服务端也
   无「只取N条」语义，仅 page_size/page_token 分页可用。**thread 场景必须客户端本地
   过滤**：降序拉取 + 本地截断。另注意普通 chat 容器只能拿到话题根消息（根消息带
   `thread_id: omt_xxx` 字段），取话题全部回复需 `container_id_type=thread` + thread_id。
5. **@提及还原**：正文中的 `@_user_1` 占位符需用响应内 `mentions[].key → name` 映射替换。
6. **速率限制**：1000 次/分钟、50 次/秒。

## 详细参考

- `references/im-v1-message-api.md` —— 官方 API 参数/错误码摘录、新版响应结构样例、
  thread 限制实测判定表（2026-09-17）、辅助接口（list-chats / 资源下载 / token 获取）。
