# Aegis Web 接入 Aegis Portal OIDC

## 认证入口

Aegis web 支持两种入口：

1. Portal“我的服务”入口：Portal 将用户带到
   `http://127.0.0.1:9130/?organization_id=<id>&client_id=<client_id>`。
   Aegis 服务端随后向 Portal 发起带组织上下文的 Authorization Code + S256
   PKCE 请求。
2. Aegis 登录页 SSO：用户点击“SSO 认证登录”，Aegis 使用配置中的
   `client_id` 并携带 `sso=1` 发起授权，不携带组织 ID。Portal 登录后自动解析
   组织；匹配多个组织时展示组织选择页。

两种流程最终都回到：

```text
http://127.0.0.1:9130/api/sso/callback
→ HttpOnly 一次性票据
→ /sso/callback
→ POST /api/sso/exchange
→ Aegis 本地 JWT
```

浏览器跳转不使用 `subscription_id`。订阅匹配由 Portal 内部完成，Aegis
只校验 Portal 返回的 client、组织和用户声明。

## 环境配置

```dotenv
OIDC_ISSUER=http://127.0.0.1:8080
OIDC_BACKCHANNEL_URL=http://127.0.0.1:8080
OIDC_CLIENT_ID=<Portal application client id>
OIDC_CLIENT_SECRET=<Portal application secret>
OIDC_REDIRECT_URI=http://127.0.0.1:9130/api/sso/callback
OIDC_POST_LOGIN_REDIRECT=/sso/callback
```

secret 只能通过运行环境注入。浏览器使用 `OIDC_ISSUER`，Aegis 进程使用
`OIDC_BACKCHANNEL_URL` 调用 Portal Token、JWKS 和 UserInfo 接口。

## 本地账号策略

- 按 OIDC `sub` 优先查找本地账号；没有 subject 时按 email 绑定普通启用账号。
- 首次登录自动创建 enabled 普通用户，用户名为 `oidc_<subject-hash>`。
- 新用户密码为随机哈希，不展示初始密码，默认仅使用 SSO。
- 停用账号、subject/email 冲突和缺少必要声明时拒绝登录。
- Portal roles 不提升 Aegis 管理员权限；本地 `admin` 账号不会按 email 自动绑定。

## 数据迁移与重启

启动时会对 `HERMES_HOME/aegis.db` 执行幂等增量迁移：为 `users` 增加可空
`oidc_subject`，并创建 OIDC 登录事务和一次性票据表。存量用户、密码和 UID
保持不变，不要求删除数据库。

```bash
hermes aegis --stop
hermes aegis --no-open
```

测试必须使用临时 `HERMES_HOME`，不能指向真实生产数据库。
