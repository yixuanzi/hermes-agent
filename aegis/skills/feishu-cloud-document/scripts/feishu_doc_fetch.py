#!/usr/bin/env python3
"""feishu_doc_fetch.py — 仅凭 URL 动态读取飞书/Lark 云文档内容（无 Hermes 依赖）。

凭证全部来自环境变量（运行时动态生效，无需重启）：
  FEISHU_APP_ID            应用 App ID（必填，除非提供 USER_ACCESS_TOKEN）
  FEISHU_APP_SECRET        应用 App Secret（必填，同上）
  FEISHU_DOMAIN            feishu | lark （必填；缺失或非法时显式报错，不推断）
  FEISHU_USER_ACCESS_TOKEN 可选；提供则以用户身份调用（可读"应用不可见但用户可见"的文档）

用法：
  python3 feishu_doc_fetch.py <文档URL或token> [--out FILE] [--json]
  python3 feishu_doc_fetch.py --test-conn     # 只验证凭证与连通性
  python3 feishu_doc_fetch.py --parse <URL>   # 只解析 URL，不调 API

支持 URL 形态：
  https://xxx.feishu.cn/docx/<token>   新版文档（raw_content 直读）
  https://xxx.feishu.cn/wiki/<token>   知识库节点（自动 get_node 解析真实 doc）
  https://xxx.feishu.cn/sheets/<token> 电子表格（每个子表渲染为 Markdown 表格，
                                       默认前 500 行，FEISHU_SHEET_MAX_ROWS 可调）
  https://xxx.feishu.cn/docs/<token>   旧版文档（提示不支持原因）
  https://xxx.larksuite.com/docx/...   Lark 国际版
  也接受裸 token。
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from urllib.parse import parse_qs, urlparse

DOMAINS = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}

# URL 中各路径前缀 -> 文档类型
URL_TYPES = {
    "docx": "docx", "doc": "doc", "docs": "doc",
    "wiki": "wiki", "sheets": "sheet", "base": "bitable",
    "mindnote": "mindnote", "file": "file", "drive": "file",
}


def env(name, default=""):
    return os.getenv(name, default).strip()


def base_url():
    domain = env("FEISHU_DOMAIN")
    if not domain:
        raise SystemExit(
            "ERROR: 未设置 FEISHU_DOMAIN（feishu | lark），无法推断目标环境，请显式配置后重试")
    url = DOMAINS.get(domain.lower())
    if not url:
        raise SystemExit(f"ERROR: FEISHU_DOMAIN 必须为 {'/'.join(DOMAINS)}，当前值无效: {domain!r}")
    return url


def api(path):
    return base_url() + path


def parse_doc_url(url: str):
    """返回 (token, doc_type)。识别不了返回 (None, None)。"""
    url = url.strip()
    m = re.search(r"/(?:docx|doc|docs|wiki|sheets|base|mindnote|file|drive)/([A-Za-z0-9]+)", url)
    if m:
        prefix = re.search(r"/(docx|doc|docs|wiki|sheets|base|mindnote|file|drive)/", url).group(1)
        return m.group(1), URL_TYPES[prefix]
    # 裸 token：wiki 节点 wik* / 新文档 docx* / 旧文档 * 按 wiki 试
    if re.fullmatch(r"[A-Za-z0-9]{10,}", url):
        if url.startswith(("wik", "docx")):
            return url, "wiki" if url.startswith("wik") else "docx"
        return url, "docx"
    return None, None


def parse_sheet_id(url: str):
    """从表格 URL 查询参数中提取可选的子表 ID。"""
    try:
        values = parse_qs(
            urlparse(url.strip()).query, keep_blank_values=True
        ).get("sheet", [])
    except ValueError:
        return None
    if not values:
        return None
    sheet_id = values[0].strip()
    if not re.fullmatch(r"[A-Za-z0-9]+", sheet_id):
        raise SystemExit(f"ERROR: sheet 参数无效: {sheet_id!r}")
    return sheet_id


def http(method: str, url: str, payload: dict = None, token: str = None, params: dict = None):
    if params:
        from urllib.parse import urlencode
        url = url + "?" + urlencode(params)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"code": e.code, "msg": f"HTTP {e.code}"}


_token_cache = {"ts": 0, "value": ""}


def get_tenant_token() -> str:
    """用 app_id/app_secret 换 tenant_access_token（缓存至过期前 5 分钟）。"""
    import time
    if _token_cache["value"] and time.time() < _token_cache["ts"]:
        return _token_cache["value"]
    app_id, app_secret = env("FEISHU_APP_ID"), env("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise SystemExit(
            "ERROR: 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，无法推断凭证，请显式配置后重试"
            "（不要把凭证写进命令行参数）")
    r = http("POST", api("/open-apis/auth/v3/tenant_access_token/internal"),
             payload={"app_id": app_id, "app_secret": app_secret})
    if r.get("code") != 0:
        raise SystemExit(f"ERROR: 获取 tenant_access_token 失败 code={r.get('code')} msg={r.get('msg')}")
    _token_cache.update(value=r["tenant_access_token"], ts=time.time() + r.get("expire", 3600) - 300)
    return _token_cache["value"]


def auth_token() -> str:
    """优先用户身份（FEISHU_USER_ACCESS_TOKEN），否则应用身份。"""
    return env("FEISHU_USER_ACCESS_TOKEN") or get_tenant_token()


def resolve_wiki(node_token: str) -> dict:
    """wiki 节点 -> 真实云文档 (obj_token, obj_type)。"""
    r = http("GET", api("/open-apis/wiki/v2/spaces/get_node"),
             token=auth_token(), params={"token": node_token})
    if r.get("code") != 0:
        raise SystemExit(f"ERROR: 解析 wiki 节点失败 code={r.get('code')} msg={r.get('msg')}")
    node = r["data"]["node"]
    return node["obj_token"], node["obj_type"], node.get("title", "")


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):  # 超链接/人物等富文本单元格
        if "text" in v:
            return str(v["text"])
        if "link" in v:
            return str(v["link"])
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):  # 富文本分段
        parts = []
        for seg in v:
            if isinstance(seg, dict):
                parts.append(_cell_str(seg.get("text", seg.get("link", ""))))
            else:
                parts.append(str(seg))
        return "".join(parts)
    return str(v)


def _render_sheet_md(sheet_meta: dict, rows: list, max_rows: int) -> str:
    """一个子表的数据行 -> Markdown 表格。"""
    grid = sheet_meta.get("grid_properties") or {}
    actual_ncol = max((len(r) for r in rows), default=0)
    ncol = actual_ncol or grid.get("column_count", 0)
    if not rows or ncol == 0:
        return f"### {sheet_meta.get('title', sheet_meta.get('sheet_id'))}\n\n（空表）\n"

    def fmt_row(r):
        cells = [_cell_str(c).replace("\n", " ") for c in (list(r) + [None] * ncol)[:ncol]]
        return "| " + " | ".join(cells) + " |"

    shown = rows[:max_rows]
    lines = [f"### {sheet_meta.get('title', sheet_meta.get('sheet_id'))}", ""]
    lines.append(fmt_row(shown[0]))
    lines.append("|" + "---|" * ncol)
    for r in shown[1:]:
        lines.append(fmt_row(r))
    if len(rows) > max_rows:
        lines.append(f"\n（仅显示前 {max_rows} 行，共 {len(rows)} 行；可用 FEISHU_SHEET_MAX_ROWS 调整）")
    lines.append("")
    return "\n".join(lines)


def read_sheet(sheets_token: str, requested_sheet_id: str = None) -> dict:
    """电子表格：元信息 -> 每个子表逐行取值 -> Markdown。"""
    max_rows = int(env("FEISHU_SHEET_MAX_ROWS", "500") or 500)
    tk = auth_token()
    hdr = {"Authorization": f"Bearer {tk}", "Content-Type": "application/json; charset=utf-8"}
    from urllib.request import Request, urlopen

    def get_json(url, params=None):
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        req = Request(url, headers=hdr)
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"code": e.code, "msg": f"HTTP {e.code}"}

    meta = get_json(api(f"/open-apis/sheets/v3/spreadsheets/{sheets_token}"))
    if meta.get("code") != 0:
        hint = "（多为权限问题：请把表格分享给本应用/机器人，或配置 FEISHU_USER_ACCESS_TOKEN）"
        raise SystemExit(f"ERROR: 获取表格元信息失败 code={meta.get('code')} msg={meta.get('msg')} {hint}")
    title = (meta.get("data") or {}).get("spreadsheet", {}).get("title", "")

    sheets_r = get_json(api(f"/open-apis/sheets/v3/spreadsheets/{sheets_token}/sheets/query"))
    if sheets_r.get("code") != 0:
        raise SystemExit(f"ERROR: 获取子表列表失败 code={sheets_r.get('code')} msg={sheets_r.get('msg')}")
    sheet_list = (sheets_r.get("data") or {}).get("sheets", [])
    if requested_sheet_id:
        sheet_list = [sh for sh in sheet_list if sh.get("sheet_id") == requested_sheet_id]
        if not sheet_list:
            raise SystemExit(f"ERROR: 未找到指定子表 sheet={requested_sheet_id}")

    parts = [f"# {title}\n"] if title else []
    for sh in sheet_list:
        grid = sh.get("grid_properties") or {}
        sid, scount = sh["sheet_id"], grid.get("row_count", 0)
        if scount == 0:
            parts.append(_render_sheet_md(sh, [], max_rows))
            continue
        fr = get_json(api(f"/open-apis/sheets/v2/spreadsheets/{sheets_token}/values/{sid}"),
                      params={"valueRenderOption": "ToString", "dateTimeRenderOption": "FormattedString"})
        if fr.get("code") != 0:
            parts.append(f"### {sh.get('title', sid)}\n\n读取失败 code={fr.get('code')} msg={fr.get('msg')}\n")
            continue
        rows = ((fr.get("data") or {}).get("valueRange") or {}).get("values", []) or []
        parts.append(_render_sheet_md(sh, rows, max_rows))

    return {"token": sheets_token, "obj_type": "sheet", "via": "sheets-v3+v2",
            "content": "\n".join(parts)}


def read_doc(url_or_token: str) -> dict:
    """主入口：URL/token -> {'title'?,'content','token','obj_type','via'}"""
    token, dtype = parse_doc_url(url_or_token)
    requested_sheet_id = parse_sheet_id(url_or_token) if dtype == "sheet" else None
    if not token:
        raise SystemExit(f"ERROR: 无法从输入中解析出文档 token: {url_or_token!r}")

    via = "direct"
    if dtype == "wiki":
        token, dtype, title = resolve_wiki(token)
        via = "wiki->get_node"

    if dtype == "sheet":
        return read_sheet(token, requested_sheet_id)

    if dtype == "docx":
        r = http("GET", api(f"/open-apis/docx/v1/documents/{token}/raw_content"), token=auth_token())
        if r.get("code") != 0:
            hint = ""
            if r.get("code") in (177003, 131006, 99991672, 99991661, 99991679):
                hint = "（多为权限问题：请把文档分享给本应用/机器人，或配置 FEISHU_USER_ACCESS_TOKEN）"
            raise SystemExit(f"ERROR: 读取文档失败 code={r.get('code')} msg={r.get('msg')}{hint}")
        return {"token": token, "obj_type": dtype, "via": via, "content": r["data"]["content"]}

    if dtype == "doc":
        raise SystemExit("ERROR: 旧版文档(doc)无 raw_content 接口；请在飞书中转换为新版文档(docx)后重试")

    raise SystemExit(f"ERROR: 暂不支持该文档类型 {dtype}（当前支持 docx / wiki->docx / sheets）")


def main():
    p = argparse.ArgumentParser(description="凭 URL 读取飞书云文档")
    p.add_argument("source", nargs="?", help="文档 URL 或 token")
    p.add_argument("--out", help="写入文件而非 stdout")
    p.add_argument("--json", action="store_true", help="输出 JSON（含 token/类型/路径）")
    p.add_argument("--parse", action="store_true", help="只解析 URL 不调 API")
    p.add_argument("--test-conn", action="store_true", help="只验证凭证与连通性")
    a = p.parse_args()

    if a.test_conn:
        t = get_tenant_token()
        print(f"OK tenant_access_token acquired via {env('FEISHU_DOMAIN', 'feishu')} "
              f"(app={env('FEISHU_APP_ID')[:8]}..., len={len(t)})")
        return
    if a.parse:
        token, dtype = parse_doc_url(a.source or "")
        print(json.dumps({"token": token, "obj_type": dtype,
                          "sheet": parse_sheet_id(a.source or "")}, ensure_ascii=False))
        return
    if not a.source:
        p.error("需要提供文档 URL（或 --test-conn）")

    result = read_doc(a.source)
    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    elif a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(result["content"])
        print(f"saved: {a.out} ({len(result['content'])} chars)")
    else:
        print(result["content"])


if __name__ == "__main__":
    main()