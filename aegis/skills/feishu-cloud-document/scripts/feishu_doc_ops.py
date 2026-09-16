#!/usr/bin/env python3
"""feishu_doc_ops.py — 飞书/Lark 云文档全操作：创建、写入、删除、权限管理。

与 feishu_doc_fetch.py（只读）同源鉴权逻辑，凭证全部来自环境变量：
  FEISHU_APP_ID / FEISHU_APP_SECRET（必填，除非提供 USER_ACCESS_TOKEN）
  FEISHU_DOMAIN   feishu | lark（必填；缺失或非法时显式报错，不推断）
  FEISHU_USER_ACCESS_TOKEN 可选；提供则以用户身份调用

用法（子命令式）：
  # 创建文档（可写入初始内容）
  python3 feishu_doc_ops.py create --title "标题" [--folder FOLDER_TOKEN]
         [--content-file FILE | --content-text "文本"]

  # 写入/追加内容到已有文档
  python3 feishu_doc_ops.py write <URL或docx_token>
         [--content-file FILE | --content-text "文本"] [--mode append|overwrite]

  # 读取内容（代理到 feishu_doc_fetch.py 的 read_doc）
  python3 feishu_doc_ops.py read <URL或token> [--json]

  # 删除文档（移入回收站）
  python3 feishu_doc_ops.py delete <URL或docx_token> [--yes]

  # 权限管理：添加成员
  python3 feishu_doc_ops.py perm add <URL或token> --member <open_id或url>
         [--role edit|view|full_access] [--type openid|userid|email|opendepartmentid]

  # 权限管理：更新成员权限
  python3 feishu_doc_ops.py perm update <URL或token> --member <open_id>
         --role edit|view|full_access

  # 权限管理：移除成员
  python3 feishu_doc_ops.py perm remove <URL或token> --member <open_id>

  # 权限管理：列出当前成员
  python3 feishu_doc_ops.py perm list <URL或token>

  # 权限管理：文档所有者让渡（需应用具备全权限，通常仅 owner 可操作）
  python3 feishu_doc_ops.py perm transfer <URL或token> --to <open_id>

  # 权限管理：修改文档公共设置（如开启"组织内可编辑"）
  python3 feishu_doc_ops.py perm public <URL或token>
         [--external true|false] [--entityedit true|false] [--entityview true|false]

  python3 feishu_doc_ops.py --test-conn
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request

DOMAINS = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}
URL_TYPES = {
    "docx": "docx", "doc": "doc", "docs": "doc",
    "wiki": "wiki", "sheets": "sheet", "base": "bitable",
    "mindnote": "mindnote", "file": "file", "drive": "file",
}
ROLES = ("view", "edit", "full_access")


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
    url = url.strip()
    m = re.search(r"/(?:docx|doc|docs|wiki|sheets|base|mindnote|file|drive)/([A-Za-z0-9]+)", url)
    if m:
        prefix = re.search(r"/(docx|doc|docs|wiki|sheets|base|mindnote|file|drive)/", url).group(1)
        return m.group(1), URL_TYPES[prefix]
    if re.fullmatch(r"[A-Za-z0-9]{10,}", url):
        if url.startswith("wik"):
            return url, "wiki"
        return url, "docx"
    return None, None


def http(method: str, url: str, payload=None, token: str = None, params: dict = None):
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


def get_tenant_token() -> str:
    app_id, app_secret = env("FEISHU_APP_ID"), env("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise SystemExit(
            "ERROR: 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，无法推断凭证，请显式配置后重试"
            "（不要把凭证写进命令行参数）")
    r = http("POST", api("/open-apis/auth/v3/tenant_access_token/internal"),
             payload={"app_id": app_id, "app_secret": app_secret})
    if r.get("code") != 0:
        raise SystemExit(f"ERROR: 获取 tenant_access_token 失败 code={r.get('code')} msg={r.get('msg')}")
    return r["tenant_access_token"]


def auth_token() -> str:
    return env("FEISHU_USER_ACCESS_TOKEN") or get_tenant_token()


def die_if_err(r: dict, action: str):
    if r.get("code") != 0:
        hint = ""
        if r.get("code") in (177003, 131006, 99991672, 99991661, 99991679, 1062004,
                             1061004, 230002, 99991663, 99991681):
            hint = "（多为权限问题：请把文档/云空间授权给本应用，或配置 FEISHU_USER_ACCESS_TOKEN）"
        raise SystemExit(f"ERROR: {action}失败 code={r.get('code')} msg={r.get('msg')}{hint}")


def resolve_to_docx(url_or_token: str):
    """wiki 节点解析为真实 docx；已是 docx 直接返回。"""
    token, dtype = parse_doc_url(url_or_token)
    if not token:
        raise SystemExit(f"ERROR: 无法解析文档 token: {url_or_token!r}")
    if dtype == "wiki":
        r = http("GET", api("/open-apis/wiki/v2/spaces/get_node"),
                 token=auth_token(), params={"token": token})
        die_if_err(r, "解析 wiki 节点")
        node = r["data"]["node"]
        return node["obj_token"], node.get("obj_type", "docx")
    if dtype in ("doc", "doc"):
        raise SystemExit("ERROR: 旧版文档(doc)不支持写入/权限管理；请先转换为新版文档(docx)")
    if dtype != "docx":
        raise SystemExit(f"ERROR: 该操作仅支持新版文档(docx)，当前类型为 {dtype}")
    return token, dtype


# ---------------------------------------------------------------- create

def md_to_blocks(md_text: str) -> list:
    """极简 Markdown -> docx blocks（标题/列表/段落）。仅支持 #/##/###、- 列表、普通段落。"""
    blocks = []
    for line in md_text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        if line.startswith("### "):
            blocks.append(_heading_block(3, line[4:].strip()))
        elif line.startswith("## "):
            blocks.append(_heading_block(2, line[3:].strip()))
        elif line.startswith("# "):
            blocks.append(_heading_block(1, line[2:].strip()))
        elif re.match(r"^[-*] ", line.strip()):
            blocks.append(_bullet_block(line.strip()[2:].strip()))
        else:
            blocks.append(_text_block(line.strip()))
    return blocks


def _run(text: str) -> dict:
    return {"text_run": {"content": text}}


def _text_block(text: str) -> dict:
    return {"block_type": 2, "text": {"elements": [_run(text)], "style": {}}}


def _heading_block(level: int, text: str) -> dict:
    return {"block_type": 2 + level,  # 3=heading1, 4=heading2 ... 11=heading9
            f"heading{level}": {"elements": [_run(text)], "style": {}}}


def _bullet_block(text: str) -> dict:
    return {"block_type": 12, "bullet": {"elements": [_run(text)], "style": {}}}


def create_doc(args):
    payload = {"folder_token": args.folder or "", "title": args.title}
    r = http("POST", api("/open-apis/docx/v1/documents"), payload=payload, token=auth_token())
    die_if_err(r, "创建文档")
    doc = r["data"]["document"]
    doc_id = doc["document_id"]
    result = {"document_id": doc_id, "title": doc.get("title", args.title),
              "url": f"https://{'larksuite' if 'larksuite' in base_url() else 'feishu'}.com/docx/{doc_id}"}
    # 写入初始内容
    content = _load_content(args)
    if content:
        blocks = md_to_blocks(content)
        if blocks:
            wr = http("POST", api(f"/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children"),
                      payload={"children": blocks, "index": 0}, token=auth_token())
            die_if_err(wr, "写入初始内容")
            result["blocks_written"] = len(blocks)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- write

def write_doc(args):
    doc_id, _ = resolve_to_docx(args.source)
    content = _load_content(args)
    if not content:
        raise SystemExit("ERROR: 需要写入内容（--content-file 或 --content-text）")
    blocks = md_to_blocks(content)

    # 追加模式：找到当前文档末尾 index
    index = 0
    if args.mode == "append":
        lr = http("GET", api(f"/open-apis/docx/v1/documents/{doc_id}/blocks"),
                  token=auth_token(), params={"page_size": 500})
        die_if_err(lr, "读取文档结构")
        items = (lr.get("data") or {}).get("items", [])
        index = max(0, len(items) - 1)  # 去掉根块后的块数
    else:
        # overwrite：先删掉所有子块再写
        lr = http("GET", api(f"/open-apis/docx/v1/documents/{doc_id}/blocks"),
                  token=auth_token(), params={"page_size": 500})
        die_if_err(lr, "读取文档结构")
        items = (lr.get("data") or {}).get("items", [])
        # overwrite：用「删除子块」API（DELETE /blocks/{doc_id}/children，
        # 按 start_index/end_index 区间删除）。注意：直接 DELETE /blocks/{block_id}
        # 路由不存在（返回 404 page not found），是历史缺陷。
        guard = 0
        while guard < 5:
            child_count = len(items) - 1  # items 含根块自身
            if child_count <= 0:
                break
            dr = http("DELETE", api(f"/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children/batch_delete"),
                      payload={"start_index": 0, "end_index": child_count}, token=auth_token())
            die_if_err(dr, "overwrite 清空旧内容")
            # 重新拉取结构验证是否已清空（可能需要多轮）
            lr = http("GET", api(f"/open-apis/docx/v1/documents/{doc_id}/blocks"),
                      token=auth_token(), params={"page_size": 500})
            die_if_err(lr, "读取文档结构")
            items = (lr.get("data") or {}).get("items", [])
            guard += 1
        if len(items) > 1:
            raise SystemExit("ERROR: overwrite 清空旧内容失败，部分块无法删除")

    r = http("POST", api(f"/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children"),
             payload={"children": blocks, "index": index}, token=auth_token())
    die_if_err(r, "写入内容")
    print(json.dumps({"document_id": doc_id, "mode": args.mode,
                      "blocks_written": len(blocks),
                      "url": f"https://{'larksuite' if 'larksuite' in base_url() else 'feishu'}.com/docx/{doc_id}"},
                     ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- read

def read_doc(args):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from feishu_doc_fetch import read_doc as _read  # 复用只读实现
    result = _read(args.source)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result["content"])


# ---------------------------------------------------------------- delete

def delete_doc(args):
    doc_id, _ = resolve_to_docx(args.source)
    if not args.yes:
        raise SystemExit("ERROR: 删除为危险操作，请加 --yes 确认（文档将移入云空间回收站）")
    r = http("DELETE", api(f"/open-apis/drive/v1/files/{doc_id}?type=docx"), token=auth_token())
    die_if_err(r, "删除文档")
    print(json.dumps({"deleted": doc_id, "note": "已移入回收站"}, ensure_ascii=False))


# ---------------------------------------------------------------- perm

def _perm_member(args_member: str):
    """支持直接给 open_id，或给用户主页/分享 URL（提取 out_id/open_id 参数）。"""
    if "open_id=" in args_member or "out_id=" in args_member:
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(args_member).query)
        for key in ("open_id", "out_id"):
            if q.get(key):
                return q[key][0], "openid"
        raise SystemExit("ERROR: 无法从 URL 提取 open_id")
    return args_member, "openid"


PERM_TYPE_MAP = {"openid": "openid", "userid": "userid", "email": "email",
                 "opendepartmentid": "opendepartmentid"}


def perm_add(args):
    doc_id, _ = resolve_to_docx(args.source)
    member, mtype = _perm_member(args.member)
    if args.type:
        mtype = PERM_TYPE_MAP[args.type]
    if args.role not in ROLES:
        raise SystemExit(f"ERROR: role 必须为 {'/'.join(ROLES)}")
    r = http("POST", api(f"/open-apis/drive/v1/permissions/{doc_id}/members?type=docx&need_notification=true"),
             payload={"member_type": mtype, "member_id": member, "perm": args.role},
             token=auth_token())
    die_if_err(r, "添加权限成员")
    print(json.dumps({"added": member, "perm": args.role, "document": doc_id}, ensure_ascii=False))


def perm_update(args):
    doc_id, _ = resolve_to_docx(args.source)
    member, mtype = _perm_member(args.member)
    if args.role not in ROLES:
        raise SystemExit(f"ERROR: role 必须为 {'/'.join(ROLES)}")
    r = http("PUT", api(f"/open-apis/drive/v1/permissions/{doc_id}/members/{member}?type=docx"),
             payload={"member_type": mtype, "perm": args.role}, token=auth_token())
    die_if_err(r, "更新成员权限")
    print(json.dumps({"updated": member, "perm": args.role, "document": doc_id}, ensure_ascii=False))


def perm_remove(args):
    doc_id, _ = resolve_to_docx(args.source)
    member, mtype = _perm_member(args.member)
    r = http("DELETE", api(f"/open-apis/drive/v1/permissions/{doc_id}/members/{member}?type=docx&member_type={mtype}"),
             token=auth_token())
    die_if_err(r, "移除权限成员")
    print(json.dumps({"removed": member, "document": doc_id}, ensure_ascii=False))


def perm_list(args):
    doc_id, _ = resolve_to_docx(args.source)
    r = http("GET", api(f"/open-apis/drive/v1/permissions/{doc_id}/members?type=docx"),
             token=auth_token())
    die_if_err(r, "列出权限成员")
    items = (r.get("data") or {}).get("items", [])
    print(json.dumps({"document": doc_id, "members": items}, ensure_ascii=False, indent=2))


def perm_transfer(args):
    """文档所有者让渡。注意：接口要求操作者本身是 owner 或具备相应管理权限。"""
    doc_id, _ = resolve_to_docx(args.source)
    member, mtype = _perm_member(args.to)
    # 飞书 transfer owner 接口
    r = http("POST", api(f"/open-apis/drive/v1/permissions/{doc_id}/members/transfer_owner?type=docx&need_notification=true&remove_perm=false"),
             payload={"member_type": mtype, "member_id": member}, token=auth_token())
    die_if_err(r, "让渡文档所有者")
    print(json.dumps({"transferred_owner_to": member, "document": doc_id}, ensure_ascii=False))


def perm_public(args):
    doc_id, _ = resolve_to_docx(args.source)
    payload = {}
    if args.external is not None:
        payload["external_access"] = args.external == "true"
    if args.entityedit is not None:
        payload["link_share_entity"] = "tenant_editable" if args.entityedit == "true" else "tenant_readable"
    if not payload:
        # 无参数 = 查询当前公共设置
        r = http("GET", api(f"/open-apis/drive/v1/permissions/{doc_id}/public?type=docx"),
                 token=auth_token())
        die_if_err(r, "查询公共权限设置")
        print(json.dumps(r.get("data", {}), ensure_ascii=False, indent=2))
        return
    r = http("PATCH", api(f"/open-apis/drive/v1/permissions/{doc_id}/public?type=docx"),
             payload=payload, token=auth_token())
    die_if_err(r, "修改公共权限设置")
    print(json.dumps({"updated_public": payload, "document": doc_id}, ensure_ascii=False))


# ---------------------------------------------------------------- helpers

def _load_content(args) -> str:
    if args.content_file:
        with open(args.content_file, encoding="utf-8") as f:
            return f.read()
    if args.content_text:
        return args.content_text
    return ""


def main():
    p = argparse.ArgumentParser(description="飞书云文档全操作：创建/写入/删除/权限管理")
    p.add_argument("--test-conn", action="store_true", help="只验证凭证与连通性")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("create", help="创建新文档")
    sp.add_argument("--title", required=True)
    sp.add_argument("--folder", help="目标文件夹 token（默认应用根目录）")
    sp.add_argument("--content-file", help="初始内容文件（Markdown 简易格式）")
    sp.add_argument("--content-text", help="初始内容文本")
    sp.set_defaults(fn=create_doc)

    sp = sub.add_parser("write", help="写入/追加内容到已有文档")
    sp.add_argument("source", help="文档 URL 或 token")
    sp.add_argument("--content-file")
    sp.add_argument("--content-text")
    sp.add_argument("--mode", choices=["append", "overwrite"], default="append")
    sp.set_defaults(fn=write_doc)

    sp = sub.add_parser("read", help="读取文档内容")
    sp.add_argument("source")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=read_doc)

    sp = sub.add_parser("delete", help="删除文档（移入回收站）")
    sp.add_argument("source")
    sp.add_argument("--yes", action="store_true", help="确认删除")
    sp.set_defaults(fn=delete_doc)

    perm = sub.add_parser("perm", help="权限管理")
    psub = perm.add_subparsers(dest="perm_cmd")

    sp = psub.add_parser("add", help="添加权限成员")
    sp.add_argument("source")
    sp.add_argument("--member", required=True, help="open_id 或含 open_id 参数的 URL")
    sp.add_argument("--role", default="edit", choices=list(ROLES))
    sp.add_argument("--type", choices=list(PERM_TYPE_MAP), help="成员类型（默认 openid）")
    sp.set_defaults(fn=perm_add)

    sp = psub.add_parser("update", help="更新成员权限")
    sp.add_argument("source")
    sp.add_argument("--member", required=True)
    sp.add_argument("--role", required=True, choices=list(ROLES))
    sp.set_defaults(fn=perm_update)

    sp = psub.add_parser("remove", help="移除权限成员")
    sp.add_argument("source")
    sp.add_argument("--member", required=True)
    sp.set_defaults(fn=perm_remove)

    sp = psub.add_parser("list", help="列出权限成员")
    sp.add_argument("source")
    sp.set_defaults(fn=perm_list)

    sp = psub.add_parser("transfer", help="让渡文档所有者")
    sp.add_argument("source")
    sp.add_argument("--to", required=True, help="新 owner 的 open_id")
    sp.set_defaults(fn=perm_transfer)

    sp = psub.add_parser("public", help="查看/修改公共权限设置")
    sp.add_argument("source")
    sp.add_argument("--external", choices=["true", "false"])
    sp.add_argument("--entityedit", choices=["true", "false"])
    sp.add_argument("--entityview", choices=["true", "false"])
    sp.set_defaults(fn=perm_public)

    a = p.parse_args()
    if a.test_conn:
        t = get_tenant_token()
        print(f"OK tenant_access_token acquired (len={len(t)})")
        return
    if not a.cmd:
        p.print_help()
        return
    if a.cmd == "perm" and not getattr(a, "perm_cmd", None):
        perm.print_help()
        return
    a.fn(a)


if __name__ == "__main__":
    main()
