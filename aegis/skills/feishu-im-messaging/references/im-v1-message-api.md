# IM v1 历史消息 API 摘录（官方文档实测整理）

> 来源：https://open.larksuite.com/document/server-docs/im-v1/message/list
> 实测时间：2026-09-17（lark 域，tenant_access_token）

## 请求

```
GET {domain}/open-apis/im/v1/messages
Header: Authorization: Bearer <tenant_access_token>
```

| 查询参数 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| container_id_type | string | 是 | `chat`（单聊+群聊） / `thread`（话题） |
| container_id | string | 是 | chat_id 或 thread_id（`omt_` 前缀） |
| start_time | string | 否 | 秒级时间戳；**thread 容器静默忽略**（实测） |
| end_time | string | 否 | 秒级时间戳；**thread 容器静默忽略**（实测） |
| sort_type | string | 否 | `ByCreateTimeAsc`（默认）/ `ByCreateTimeDesc`；翻页中途不可换 |
| page_size | int | 否 | 1~50，默认 20 |
| page_token | string | 否 | 翻页标记，首次不传 |

频率限制：1000 次/分钟、50 次/秒。
权限：默认仅单聊(p2p)；群组需「获取群组中所有消息」权限且机器人在群内。

## 响应结构（新版，实测样例）

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "items": [
      {
        "message_id": "om_xxx",
        "chat_id": "oc_xxx",
        "thread_id": "omt_xxx",              // 话题消息才有
        "thread_message_position": "-1",
        "message_position": "59",
        "msg_type": "text|post|interactive|system|image|...",
        "create_time": "1789651852236",       // 18位: 秒*1000+毫秒拼接（或13位毫秒）
        "update_time": "1789651861013",
        "updated": true,
        "deleted": false,
        "sender": {"id": "ou_xxx", "id_type": "open_id", "sender_type": "user", "tenant_key": "..."},
        "body": {"content": "{\"text\":\"...\"}"},   // ⚠️ content 在 body 下，且为 JSON 字符串
        "mentions": [{"key": "@_user_1", "id": "ou_xxx", "name": "Aegis", "id_type": "open_id"}]
      }
    ],
    "page_token": "...",
    "has_more": true
  }
}
```

### msg_type 与 content 结构

- `text`: `{"text": "@_user_1 who are you"}`（@占位符见 mentions）
- `post`: `{"title": "", "content": [[{"tag":"text","text":"...","style":[]}, ...]]}`（二维段落数组）
- `system`: `{"template": "{from_user} invited {to_chatters}...", "from_user": [...], "to_chatters": [...]}`
- `interactive`: `{"title": "🤖 Aegis", "elements": [[{"tag":"img","image_key":"img_v3_..."}, ...]]}`

### create_time 换算（两种长度都出现）

- 18 位 = 秒时间戳 ×1000 再 +毫秒拼接：`int(ts[:10])` 得秒，`ts[14:18]` 得毫秒
- 13 位 = 标准毫秒时间戳：`int(ts[:10])` 得秒，`ts[10:13]` 得毫秒

## 错误码

| HTTP | code | 含义 |
|------|------|------|
| 400 | 230001 | 参数错误 |
| 400 | 230002 | 机器人不在群内 |
| 400 | 230006 | 机器人能力未启用 |
| 400 | 230027 | 缺权限（如「获取群组中所有消息」）；外部群不支持 |
| 400 | 230073 | 话题对操作者不可见 |
| 400 | 230110 | 消息已删除 |

## thread 容器限制（实测判定，2026-09-17）

| 能力 | 支持情况 |
|------|----------|
| 时间过滤 start_time/end_time | ❌ **静默忽略**：传入落在消息间的时间点，早于它的消息仍全部返回，不报错、不过滤 |
| 服务端条数限制 | ❌ 无「只取N条」参数；仅 page_size/page_token 分页可用 |
| 排序 + 翻页 | ✅ 正常（sort_type 与 chat 容器一致） |

**实现建议**：thread 场景在客户端本地过滤——降序拉取 + 本地截断/时间过滤；chat 容器可依赖服务端 start_time。

## 辅助接口

- 列出机器人所在会话：`GET /open-apis/im/v1/chats?page_size=100`（page_token 翻页）→ chat_id/name/owner_id
- 获取消息中的资源文件（卡片图片等）：`GET /open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=image`
- 获取 tenant_access_token：`POST /open-apis/auth/v3/tenant_access_token/internal`（app_id/app_secret，缓存至过期前5分钟）
