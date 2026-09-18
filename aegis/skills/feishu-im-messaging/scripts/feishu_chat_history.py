#!/usr/bin/env python3
"""feishu/lark 群组历史消息读取工具。

基于 GET /open-apis/im/v1/messages（获取会话历史消息）实现：
  https://open.larksuite.com/document/server-docs/im-v1/message/list

用法：
  # 列出机器人所在的群（找 container_id）
  python3 feishu_chat_history.py --list-chats

  # 读取某群全部历史消息（自动分页，输出 JSONL 到 stdout）
  python3 feishu_chat_history.py --chat oc_xxx

  # 读取最近 1 天、降序、前 50 条
  python3 feishu_chat_history.py --chat oc_xxx --last-hours 24 --desc --limit 50

  # 读取话题（thread）内消息
  python3 feishu_chat_history.py --thread om_xxx

  # 展开合并转发消息（merge_forward）的全部子消息
  python3 feishu_chat_history.py --merge-forward om_xxx
  python3 feishu_chat_history.py --merge-forward om_xxx --raw   # 完整 JSON

环境变量（本机 .env 已配置）：FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_DOMAIN
注意：读取群组消息需要应用具备「获取群组中所有消息」权限，且机器人必须在群内。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DOMAINS = {"feishu": "https://open.feishu.cn", "lark": "https://open.larksuite.com"}


def env(name, default=""):
    v = os.environ.get(name, default)
    if v and len(v) > 2 and v[0] in "'\"" and v[-1] == v[0]:
        v = v[1:-1]
    return v


def domain():
    return DOMAINS.get(env("FEISHU_DOMAIN", "feishu").lower(), DOMAINS["feishu"])


def http(method, url, payload=None, headers=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


_token_cache = {"value": None, "ts": 0}


def tenant_token():
    if _token_cache["value"] and time.time() < _token_cache["ts"]:
        return _token_cache["value"]
    app_id, app_secret = env("FEISHU_APP_ID"), env("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        sys.exit("ERROR: 需要环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET")
    r = http("POST", f"{domain()}/open-apis/auth/v3/tenant_access_token/internal",
             payload={"app_id": app_id, "app_secret": app_secret})
    if r.get("code") != 0:
        sys.exit(f"ERROR: 获取 tenant_access_token 失败 code={r.get('code')} msg={r.get('msg')}")
    _token_cache.update(value=r["tenant_access_token"],
                        ts=time.time() + r.get("expire", 3600) - 300)
    return _token_cache["value"]


def auth_headers():
    return {"Authorization": f"Bearer {tenant_token()}"}


def list_chats(page_size=100):
    """列出机器人所在的会话（用于查找 chat_id）。"""
    url = f"{domain()}/open-apis/im/v1/chats?page_size={page_size}"
    while url:
        r = http("GET", url, headers=auth_headers())
        if r.get("code") != 0:
            sys.exit(f"ERROR: 列出群组失败 code={r.get('code')} msg={r.get('msg')}")
        for c in r["data"].get("items", []):
            yield c
        token = r["data"].get("page_token")
        url = (f"{domain()}/open-apis/im/v1/chats?page_size={page_size}"
               f"&page_token={urllib.parse.quote(token)}") if r["data"].get("has_more") else None


def fetch_history(container_id_type, container_id, start_time=None, end_time=None,
                  sort="ByCreateTimeAsc", limit=None, max_pages=200):
    """按 container 分页拉取历史消息，yield 每条消息 dict。"""
    url = (f"{domain()}/open-apis/im/v1/messages"
           f"?container_id_type={container_id_type}&container_id={container_id}"
           f"&sort_type={sort}&page_size=50")
    if start_time:
        url += f"&start_time={start_time}"
    if end_time:
        url += f"&end_time={end_time}"
    count, pages = 0, 0
    while url and pages < max_pages:
        r = http("GET", url, headers=auth_headers())
        if r.get("code") != 0:
            sys.exit(f"ERROR: 获取历史消息失败 code={r.get('code')} msg={r.get('msg')}\n"
                     f"提示：群组消息需权限「获取群组中所有消息」且机器人在群内(230002/230027)")
        data = r["data"]
        for m in data.get("items", []):
            yield m
            count += 1
            if limit and count >= limit:
                return
        pages += 1
        token = data.get("page_token")
        url = (f"{domain()}/open-apis/im/v1/messages"
               f"?container_id_type={container_id_type}&container_id={container_id}"
               f"&sort_type={sort}&page_size=50"
               + (f"&start_time={start_time}" if start_time else "")
               + (f"&end_time={end_time}" if end_time else "")
               + (f"&page_token={urllib.parse.quote(token)}" if token else "")
               ) if data.get("has_more") and token else None


def fetch_merge_forward(message_id):
    """读取合并转发消息（merge_forward）的全部子消息。

    会话历史列表中 merge_forward 仅有占位文本；改用「获取指定消息」接口
    GET /open-apis/im/v1/messages/{message_id}，返回 data.items 数组：
    第 1 条为父消息本体，其后为全部子消息（带 upper_message_id 指向父消息）。
    返回 (父消息, [子消息...])。
    """
    try:
        r = http("GET", f"{domain()}/open-apis/im/v1/messages/{message_id}",
                 headers=auth_headers())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
            sys.exit(f"ERROR: 获取消息失败 HTTP {e.code} "
                     f"code={detail.get('code')} msg={detail.get('msg')}")
        except Exception:
            sys.exit(f"ERROR: 获取消息失败 HTTP {e.code} {e.reason}")
    if r.get("code") != 0:
        sys.exit(f"ERROR: 获取消息失败 code={r.get('code')} msg={r.get('msg')}")
    items = (r.get("data") or {}).get("items", [])
    if not items:
        sys.exit(f"ERROR: 消息 {message_id} 无返回 items")
    parent, subs = items[0], items[1:]
    if parent.get("msg_type") != "merge_forward":
        sys.exit(f"ERROR: 消息 {message_id} 不是 merge_forward 类型"
                 f"（实际为 {parent.get('msg_type')}），无需展开")
    return parent, subs


def resolve_thread_id(thread_id):
    """兼容传入根消息 ID（om_ 前缀）：查该消息拿真实 thread_id（omt_ 前缀）。"""
    if not thread_id or thread_id.startswith("omt_"):
        return thread_id
    mid = thread_id.strip()
    if not mid.startswith("om_"):
        sys.exit(f"ERROR: --thread 需要线程 ID（omt_）或根消息 ID（om_），收到: {thread_id}")
    r = http("GET", f"{domain()}/open-apis/im/v1/messages/{mid}", headers=auth_headers())
    if r.get("code") != 0:
        sys.exit(f"ERROR: 查询根消息失败 code={r.get('code')} msg={r.get('msg')}")
    item = (r.get("data") or {}).get("items", [{}])[0]
    tid = item.get("thread_id") or item.get("root_id")
    if not tid:
        sys.exit(f"ERROR: 消息 {mid} 不属于任何话题（无 thread_id），请直接用 --chat 读取")
    return tid


def brief(m):
    """压缩消息为可读摘要行（新版 API content 位于 body.content）。"""
    raw = (m.get("body") or {}).get("content") or m.get("content") or "{}"
    try:
        content = json.loads(raw)
    except json.JSONDecodeError:
        content = {"raw": raw}
    if m.get("msg_type") == "system":
        text = content.get("template", "")
    else:
        text = (content.get("text") or content.get("title")
                or json.dumps(content, ensure_ascii=False)[:80])
    # 还原 @人 为可读名称
    for men in m.get("mentions") or []:
        text = text.replace(men.get("key", ""), f'@{men.get("name", "?")}')
    ts = m.get("create_time", "")
    if len(ts) == 18:      # 秒+毫秒拼接格式：s*1000+ms
        ts = f'{ts[:10]}.{ts[14:18]}'
    elif len(ts) == 13:    # 毫秒时间戳
        ts = f'{ts[:10]}.{ts[10:13]}'
    ts = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(int(ts[:10]))) + \
        f'.{ts[11:15] if len(ts) > 11 else "0000"}' if ts else ''
    return f'{ts} [{m.get("msg_type")}] {text}'


def main():
    ap = argparse.ArgumentParser(description="feishu/lark 会话历史消息读取")
    ap.add_argument("--list-chats", action="store_true", help="列出机器人所在会话")
    ap.add_argument("--chat", help="chat_id（单聊或群聊）")
    ap.add_argument("--thread", help="thread_id（话题）")
    ap.add_argument("--merge-forward", metavar="MESSAGE_ID",
                    help="读取合并转发消息(om_)的全部子消息")
    ap.add_argument("--start", help="起始秒级时间戳")
    ap.add_argument("--end", help="结束秒级时间戳")
    ap.add_argument("--last-hours", type=float, help="最近 N 小时（转 start_time）")
    ap.add_argument("--desc", action="store_true", help="按创建时间降序")
    ap.add_argument("--limit", type=int, help="最多输出条数")
    ap.add_argument("--raw", action="store_true", help="输出完整 JSON 而非摘要")
    args = ap.parse_args()

    if args.list_chats:
        for c in list_chats():
            print(json.dumps({k: c.get(k) for k in
                              ("chat_id", "name", "chat_type", "owner_id")},
                             ensure_ascii=False))
        return

    if args.merge_forward:
        parent, subs = fetch_merge_forward(args.merge_forward)
        if args.raw:
            print(json.dumps({"parent": parent, "sub_messages": subs},
                             ensure_ascii=False))
        else:
            n = len(subs)
            print(f"# 合并转发消息 {parent['message_id']} 共 {n} 条子消息：")
            for m in subs:
                print(brief(m))
        print(f"--- 共 {len(subs)} 条子消息 ---", file=sys.stderr)
        return

    if not args.chat and not args.thread:
        ap.error("需要 --chat、--thread、--merge-forward 或 --list-chats 之一")
    if args.thread and (args.start or args.end or args.last_hours):
        ap.error("thread 容器暂不支持时间范围过滤（官方限制）")

    ctype, cid = ("thread", resolve_thread_id(args.thread)) if args.thread else ("chat", args.chat)
    start = args.start or (str(int(time.time() - args.last_hours * 3600))
                           if args.last_hours else None)
    sort = "ByCreateTimeDesc" if args.desc else "ByCreateTimeAsc"

    n = 0
    for m in fetch_history(ctype, cid, start_time=start, end_time=args.end,
                           sort=sort, limit=args.limit):
        print(json.dumps(m, ensure_ascii=False) if args.raw else brief(m))
        n += 1
    print(f"--- 共 {n} 条 ---", file=sys.stderr)


if __name__ == "__main__":
    main()
