# 飞书/Lark Bot 动态获取用户信息操作手册

> **作者：** Aegis 安全中枢  
> **更新日期：** 2026-08-04  
> **适用平台：** Lark（国际版）/ 飞书（国内版）  
> **API 版本：** Open API v3

---

## 目录

1. [前置条件](#1-前置条件)
2. [获取 Tenant Access Token](#2-获取-tenant-access-token)
3. [三种用户 ID 类型详解](#3-三种用户-id-类型详解)
4. [权限清单与依赖关系](#4-权限清单与依赖关系)
5. [API 详解：获取单个用户信息](#5-api-详解获取单个用户信息)
6. [API 详解：批量获取用户信息](#6-api-详解批量获取用户信息)
7. [API 详解：列出所有用户](#7-api-详解列出所有用户)
8. [常见错误及排查](#8-常见错误及排查)
9. [完整工作流示例](#9-完整工作流示例)
10. [FAQ](#10-faq)

---

## 1. 前置条件

| 条件 | 说明 |
|------|------|
| Bot App | 已在 [Lark Developer Console](https://open.larksuite.com/app) 或[飞书开放平台](https://open.feishu.cn/app) 创建应用 |
| App ID / App Secret | 应用凭证，用于获取 `tenant_access_token` |
| 权限（Scope） | 根据需要授予（见第 4 节），需发布应用版本后生效 |
| 网络 | 可访问飞书 API 端点 |

**域名区别：**

| 环境 | 域名 |
|------|------|
| Lark（国际版） | `https://open.larksuite.com` |
| 飞书（国内版） | `https://open.feishu.cn` |

> ⚠️ 以下文档以 Lark 国际版为例，飞书用户只需要将 `larksuite.com` 替换为 `feishu.cn` 即可。

---

## 2. 获取 Tenant Access Token

所有用户查询 API 都需要携带 `tenant_access_token` 进行鉴权。

### 请求

```
POST https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal
Content-Type: application/json

{
  "app_id": "cli_xxxxxxxxxxxxxxxxxx",
  "app_secret": "xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

### 响应

```json
{
  "code": 0,
  "msg": "ok",
  "tenant_access_token": "t-g20684aSDSWN5V7IKT7DTAOYX6UV3ZFRYXBBYIHZ",
  "expire": 7200
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | int | `0` 表示成功 |
| `tenant_access_token` | string | 后续 API 调用使用的令牌 |
| `expire` | int | 过期时间，单位秒（默认 7200s） |

> ⚠️ **Token 有效期为 2 小时**，建议缓存并在过期前刷新。所有后续 API 请求需携带请求头：
>
> ```
> Authorization: Bearer {tenant_access_token}
> ```

---

## 3. 三种用户 ID 类型详解 ⚠️ 易错重点

飞书/Lark 有三种不同类型的用户标识符，这是最容易出错的环节。

### 3.1 ID 类型对照

| ID 类型 | 前缀 | 示例 | 说明 | 作用域 |
|---------|------|------|------|--------|
| **open_id** | `ou_` | `ou_d5c922ac89c0ecbed9889beaa9eaf3e9` | 用户在该应用内的唯一 ID | 只在当前应用内有效 |
| **union_id** | `on_` | `on_21726264c0784c125ebe32724bb4c43f` | 用户在同一开放平台开发者账号下的全局 ID | 跨应用共享 |
| **user_id** | 纯数字/字符串 | `7gb52a3` | 企业的员工工号/员工 ID | 企业内唯一 |

### 3.2 ❗ 最容易踩的坑：平台内部 ID 与 Lark API ID 的混淆

**这是最常见的错误，也是我们之前遇到的问题的核心：**

#### 错误的做法（你正在使用的飞书平台可能会暴露这类 ID）

```
用户ID = "bcge9g7d"
```

像 `bcge9g7d` 这样的 ID，是 **Hermes Agent 内部存储的平台级用户标识符**，来源于飞书 WebSocket 事件推送中的 `user_id` / `open_id` 字段。但注意：

- **飞书 WebSocket 事件**中的 `sender.sender_id.user_id` 并不一定是 API 可识别的 `open_id`
- 在 DM（私信）场景下，这个 ID 可能是 Lark 的 `open_id`，也可能是 `user_id`，取决于 Bot 配置和消息来源
- **Hermes 的 session 记录中 `user_id` 字段存储的值并不保证就是 Lark API 能直接识别的 `open_id`**

✅ **正确的做法：**

1. **从 WebSocket 事件中查看 `user_id_alt`**（如果平台有提供），或从消息事件的 `event.header.event_id` 推导
2. **如果知道 `union_id`，可以用 union_id 查询**
3. **最可靠的方法：直接调用 List Users API 获取当前租户下所有用户列表，从中找到匹配的用户**
4. **得到正确的 open_id 后（以 `ou_` 开头），再用于单用户查询 API**

#### 如何从消息事件中获取正确的 open_id

飞书 WebSocket 消息推送的 JSON 结构通常包含：

```json
{
  "event": {
    "sender": {
      "sender_id": {
        "union_id": "on_21726264c0784c125ebe32724bb4c43f",
        "user_id": "bcge9g7d",
        "open_id": "ou_d5c922ac89c0ecbed9889beaa9eaf3e9"
      }
    },
    "message": {
      "chat_id": "oc_xxxxxxxxxxxx",
      "message_type": "text"
    }
  }
}
```

在这里：
- `open_id`（`ou_` 开头）→ **可直接用于联系人 API**
- `union_id`（`on_` 开头）→ **可直接用于联系人 API**
- `user_id` → **可能是员工 ID 或平台内部 ID，不一定能直接用于 API**

### 3.3 如何判断当前手上的 ID 是什么类型

| ID 开头 | 类型 | 可直接查询？ |
|---------|------|-------------|
| `ou_` | open_id | ✅ 可 |
| `on_` | union_id | ✅ 可 |
| `bcge9g7d` 之类的短 ID | 未知 | ❌ 不可直接查，先用 List 接口寻找 |
| 纯数字 | 可能是 user_id | 不确定，先试或换 ID 类型 |

---

## 4. 权限清单与依赖关系

不同的用户字段需要不同的权限范围（Scope）。应用必须在开发者后台**申请权限 → 发布版本 → 管理员审批**后才会生效。

### 4.1 权限范围详解

| 权限 Scope | 可获取的字段 | 说明 |
|-----------|------------|------|
| `contact:user.base:readonly` | `open_id`, `union_id`, `user_id`, `name`, `en_name`, `avatar`, `description` | ✅ 基础权限，通常默认开启 |
| `contact:user.email:readonly` | `email` | ✅ 获取邮箱（可能需要审批） |
| `contact:user.phone:readonly` | `mobile` | ✅ 获取手机号（需要审批） |
| `contact:user.employee_id:readonly` | `employee_id`（员工编号） | ✅ 获取员工 ID |
| `contact:user.batch:readonly` | 批量查询 | ✅ 支持批量接口 |

### 4.2 权限与字段矩阵

| 字段 | 所需权限 | 默认返回？ |
|------|---------|-----------|
| `name` | `contact:user.base:readonly` | ✅ 是 |
| `en_name` | `contact:user.base:readonly` | ✅ 是 |
| `open_id` | `contact:user.base:readonly` | ✅ 是 |
| `union_id` | `contact:user.base:readonly` | ✅ 是 |
| `avatar` | `contact:user.base:readonly` | ✅ 是 |
| `description` | `contact:user.base:readonly` | ✅ 是 |
| `email` | `contact:user.email:readonly` | ❌ 需额外权限 |
| `mobile` | `contact:user.phone:readonly` | ❌ 需额外权限 |
| `employee_id` | `contact:user.employee_id:readonly` | ❌ 需额外权限 |

### 4.3 权限错误示例

当权限不足时，API 会返回：

```json
{
  "code": 99991672,
  "msg": "Access denied. One of the following scopes is required: [contact:user.email:readonly]",
  "error": {
    "permission_violations": [
      {
        "type": "field_scope_required",
        "subject": "contact:user.email:readonly"
      }
    ]
  }
}
```

### 4.4 权限申请流程

1. 登录 [Lark Developer Console](https://open.larksuite.com/app) → 选择应用
2. 左侧菜单 **「Permissions」** → **「API Permissions」**
3. 搜索并添加需要的权限（如 `contact:user.email:readonly`）
4. 点击 **「Create Version」** 创建新版本
5. 提交审核并发布
6. 企业管理员审批后，新权限生效

---

## 5. API 详解：获取单个用户信息

### 请求

```
GET https://open.larksuite.com/open-apis/contact/v3/users/{user_id}
Authorization: Bearer {tenant_access_token}
```

### 路径参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_id` | string | ✅ | 用户的 ID，根据 `user_id_type` 参数决定类型 |

### Query 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `user_id_type` | string | ❌ | `open_id` | 指定路径参数的类型：`open_id` / `union_id` / `user_id` |
| `department_id_type` | string | ❌ | `open_department_id` | 部门 ID 类型，不影响用户信息 |

### 请求示例

```bash
# 使用 open_id 查询
curl -H "Authorization: Bearer t-g20684aSDSWN5V7..." \
  "https://open.larksuite.com/open-apis/contact/v3/users/ou_d5c922ac89c0ecbed9889beaa9eaf3e9?user_id_type=open_id"
```

### 响应结构

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "user": {
      "name": "张三",
      "en_name": "San Zhang",
      "email": "zhangsan@company.com",
      "mobile": "+8613800138000",
      "open_id": "ou_d5c922ac89c0ecbed9889beaa9eaf3e9",
      "union_id": "on_21726264c0784c125ebe32724bb4c43f",
      "user_id": "7gb52a3",
      "employee_id": "E00123",
      "description": "",
      "mobile_visible": true,
      "avatar": {
        "avatar_72": "https://...",
        "avatar_240": "https://...",
        "avatar_640": "https://...",
        "avatar_origin": "https://..."
      }
    }
  }
}
```

### 错误码

| code | msg | 说明 |
|------|-----|------|
| 0 | success | 成功 |
| 99992351 | `not a valid {open_id} or not exists` | 传入的 ID 格式不对或不存在（见第 8 节排查） |
| 99991672 | `Access denied. ... scope required` | 权限不足（见第 4 节） |
| 99991661 | `user not found` | 用户不存在 |

---

## 6. API 详解：批量获取用户信息

### 请求

```
GET https://open.larksuite.com/open-apis/contact/v3/users/batch
Authorization: Bearer {tenant_access_token}
```

### Query 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_ids` | string | ✅ | 逗号分隔的用户 ID 列表，最多 50 个 |
| `user_id_type` | string | ❌ | `open_id` / `union_id` / `user_id`，默认 `open_id` |

### 请求示例

```bash
curl -H "Authorization: Bearer t-g20684..." \
  "https://open.larksuite.com/open-apis/contact/v3/users/batch?user_ids=ou_xxx1,ou_xxx2&user_id_type=open_id"
```

### 响应

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "users": [
      {
        "name": "张三",
        "open_id": "ou_xxx1",
        "email": "zhangsan@company.com",
        ...
      },
      {
        "name": "李四",
        "open_id": "ou_xxx2",
        ...
      }
    ]
  }
}
```

---

## 7. API 详解：列出所有用户（枚举方式获取用户列表）

当你手上有 `user_id` 但不确定其类型，或者需要通过查询来定位用户时，这个方法最有价值。

### 请求

```
GET https://open.larksuite.com/open-apis/contact/v3/users
Authorization: Bearer {tenant_access_token}
```

### Query 参数

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `page_size` | int | ❌ | 50 | 每页数量，最大 100 |
| `page_token` | string | ❌ | - | 分页标记，从上一页响应中获取 |
| `user_id_type` | string | ❌ | `open_id` | 响应中返回的 ID 类型 |
| `department_id` | string | ❌ | 全公司 | 按部门筛选 |

### 请求示例

```bash
# 获取第一页，每页获取 50 个用户
curl -H "Authorization: Bearer t-g20684..." \
  "https://open.larksuite.com/open-apis/contact/v3/users?page_size=50"
```

### 响应结构

```json
{
  "code": 0,
  "data": {
    "has_more": true,
    "page_token": "xxxxx",
    "items": [
      {
        "name": "Guisheng Guo",
        "en_name": "",
        "open_id": "ou_d5c922ac89c0ecbed9889beaa9eaf3e9",
        "union_id": "on_21726264c0784c125ebe32724bb4c43f",
        "avatar": { ... }
      }
    ]
  },
  "msg": "success"
}
```

### 分页处理

如果 `has_more` 为 `true`，携带返回的 `page_token` 请求下一页：

```
GET https://open.larksuite.com/open-apis/contact/v3/users?page_size=50&page_token={page_token}
```

---

## 8. 常见错误及排查 ⚠️

### 8.1 `99992351` — ID 不存在或格式错误

```json
{
  "code": 99992351,
  "msg": "The request you send is not a valid {open_id} or not exists, and the example value is {ou_da5****************dfe}. Invalid ids: [bcge9g7d]"
}
```

**原因：** 传入了一个乱写的或错误的 ID。比如把平台内部 ID `bcge9g7d` 当成了 `open_id`。

**排查步骤：**

1. 检查 ID 是否以正确的**前缀**开头（`ou_` 为 open_id，`on_` 为 union_id）
2. 如果没有前缀，它可能是 `user_id`（员工 ID），需要设置 `user_id_type=user_id`
3. 如果尝试了所有类型都失败，说明手上的 ID 不能直接使用，需要：
   - 调用 **List Users API** 枚举全公司用户列表
   - 通过姓名或其他信息人工匹配
4. 如果在 Hermes 环境下，可从 `user_id_alt`（如果有）中获取 `union_id`，然后用 union_id 查询

### 8.2 `99991672` — 权限不足

```json
{
  "code": 99991672,
  "msg": "Access denied. One of the following scopes is required: [contact:user.email:readonly]"
}
```

**原因：** 应用没有申请对应权限，或权限已申请但未发布/审批。

**排查步骤：**
1. 登录开发者后台检查应用权限列表
2. 确认权限已添加到应用
3. 确认已创建版本并发布
4. 确认企业管理员已审批

### 8.3 `99991661` — 用户不存在

**原因：** ID 格式正确（前缀正确），但确实不是该租户下的用户。

**排查步骤：**
1. 确认该用户是否还在企业通讯录中
2. 确认 ID 是否过期（如员工已离职）
3. 通过 List Users 接口验证该 ID 是否存在

### 8.4 邮箱字段始终为空（不是错误）

如果 API 返回 200 且 code=0，但响应中没有 `email` 字段：

```json
{
  "code": 0,
  "data": {
    "user": {
      "name": "张三",
      "open_id": "ou_xxx",
      "union_id": "on_xxx"
      // 没有 email 字段！
    }
  }
}
```

**原因：** 没有 `contact:user.email:readonly` 权限。API 不会报错，而是直接省略该字段。

---

## 9. 完整工作流示例

以下是从零开始获取用户信息的完整流程（Python）：

```python
import requests

APP_ID = "cli_xxxxxxxxxxxxxxxx"
APP_SECRET = "xxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# Step 1: 获取 tenant_access_token
resp = requests.post(
    "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal",
    json={"app_id": APP_ID, "app_secret": APP_SECRET}
)
token = resp.json()["tenant_access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Step 2: 如果手上有一个不确定类型的 user_id（如 "bcge9g7d"）
# 先尝试用 union_id 查询（如果已知）
resp = requests.get(
    "https://open.larksuite.com/open-apis/contact/v3/users/on_21726264c0784c125ebe32724bb4c43f",
    headers=headers,
    params={"user_id_type": "union_id"}
)

# Step 3: 如果手头没有任何可用 ID，用 List API 枚举用户
resp = requests.get(
    "https://open.larksuite.com/open-apis/contact/v3/users",
    headers=headers,
    params={"page_size": 50}
)
users = resp.json()["data"]["items"]

# Step 4: 找到目标用户的 open_id，然后获取详细信息
target_open_id = "ou_d5c922ac89c0ecbed9889beaa9eaf3e9"
resp = requests.get(
    f"https://open.larksuite.com/open-apis/contact/v3/users/{target_open_id}",
    headers=headers,
    params={"user_id_type": "open_id"}
)
user = resp.json()["data"]["user"]
print(f"Name: {user.get('name')}")
print(f"Email: {user.get('email', '⚠️ 需要 contact:user.email:readonly 权限')}")
```

### Python 函数封装

```python
class LarkUserQuery:
    """Lark 用户查询工具类"""
    
    def __init__(self, app_id: str, app_secret: str, domain: str = "larksuite.com"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = f"https://open.{domain}/open-apis"
        self._token = None
    
    def _get_token(self) -> str:
        """获取或刷新 tenant_access_token"""
        resp = requests.post(
            f"{self.base_url}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret}
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"Token 获取失败: {data.get('msg')}")
        self._token = data["tenant_access_token"]
        return self._token
    
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}
    
    def list_users(self, page_size: int = 50, page_token: str = None) -> dict:
        """列出所有用户"""
        params = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(
            f"{self.base_url}/contact/v3/users",
            headers=self._headers(),
            params=params
        )
        return resp.json()
    
    def get_user(self, user_id: str, id_type: str = "open_id") -> dict:
        """获取单个用户信息
        
        Args:
            user_id: 用户 ID
            id_type: ID 类型, 可选 open_id / union_id / user_id
            
        Returns:
            用户信息字典, 包含 name, email(如有权限), open_id, union_id 等
        """
        resp = requests.get(
            f"{self.base_url}/contact/v3/users/{user_id}",
            headers=self._headers(),
            params={"user_id_type": id_type}
        )
        return resp.json()
    
    def resolve_user_id(self, raw_id: str) -> str:
        """尝试识别并解析用户 ID
        
        支持自动识别 open_id (ou_xxx) 和 union_id (on_xxx)。
        对于未知格式的 ID，需要通过枚举用户列表来匹配。
        
        Args:
            raw_id: 原始用户 ID
            
        Returns:
            识别到的 ID 类型和值
        """
        if raw_id.startswith("ou_"):
            return ("open_id", raw_id)
        elif raw_id.startswith("on_"):
            return ("union_id", raw_id)
        else:
            return ("unknown", raw_id)
```

---

## 10. FAQ

### Q1: 为什么我拿着飞书消息事件里的 `user_id` 去查询，结果 400 错误？

**A：** 消息事件中的 `sender.sender_id` 字段包含三种 ID（`open_id`、`union_id`、`user_id`），其中 `user_id` 可能是员工 ID 或平台内部 ID。你应该使用 **`open_id`**（以 `ou_` 开头）或 **`union_id`**（以 `on_` 开头）来调用联系人 API，而不是使用 `user_id`。

### Q2: 为什么 List Users API 返回的用户列表没有 email？

**A：** 应用没有 `contact:user.email:readonly` 权限。参见第 4 节进行权限申请。

### Q3: `open_id` 和 `union_id` 有什么区别？什么时候用哪个？

**A：** `open_id` 是用户在该应用内的唯一标识；`union_id` 是你在该开放平台账号下所有应用的共享标识。调用联系人 API 时两者都可以用，只需要在 `user_id_type` 参数中指明类型即可。

### Q4: 如何验证自己的应用有哪些权限？

**A：** 调用以下接口查看当前 Token 的权限列表：

```
GET https://open.larksuite.com/open-apis/auth/v3/app_access_token/internal
```

但需要说明的是，这个返回的是应用本身的 scope 配置。更可靠的方式是直接登录 [Lark Developer Console](https://open.larksuite.com/app) → 选择应用 → Permissions → API Permissions 查看。

### Q5: 为什么我在开发者后台加了权限，但 API 还是报权限不足？

**A：** 添加权限后需要：
1. 创建一个新的版本（Create Version）
2. 提交审核并发布（Submit for Review & Publish）
3. 企业管理员在管理后台审批

发布之后新的 Token 才会携带新权限。

---

> **附录：** 本文档对应的实际验证过程记录在 Aegis 会话日志中（2026-08-04），其中成功使用 Lark Bot Token 获取了用户的名称（Guisheng Guo），因缺少 `contact:user.email:readonly` 权限未能获取邮箱。