"""ext-tools / cron_prompt 验证测试（只读，不修改真实数据）。

用例：
  1. 无 identify 的任务 → 直接返回全量 prompt
  2. 有 identify 且 Owner 匹配 → 放行
  3. 有 identify 但调用者不匹配 → 拒绝（即使调用者是"管理员"——本工具无角色豁免）
  4. 任务不存在 / 重名 → 明确报错
  5. 无身份（CLI 等场景）读带 identify 的任务 → 拒绝
"""
import json
import sys
from pathlib import Path
from unittest import mock

REPO = Path("/Users/guisheng.guo/.hermes/hermes-agent")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "plugins" / "ext-tools"))

import cron_prompt as cp  # noqa: E402
from tools.user_env_runtime import UserEnvIdentity  # noqa: E402

JOBS = [
    {  # 无 identify
        "id": "aaa1", "name": "no-owner", "prompt": "P-NO-OWNER",
        "schedule_display": "0 9 * * *", "state": "scheduled",
    },
    {  # 有 identify，owner = slack:U1
        "id": "bbb2", "name": "owned", "prompt": "P-SECRET",
        "schedule_display": "0 9 * * *", "state": "paused",
        "identify": {"platform": "slack", "user_id": "U1", "user_name": "Owner"},
    },
    {  # 与 owned 无关的第三个任务，制造重名不涉及；重名用例单独构造
        "id": "ccc3", "name": "dup", "prompt": "P1", "schedule_display": "*", "state": "paused",
    },
    {  # 与 ccc3 重名
        "id": "ddd4", "name": "dup", "prompt": "P2", "schedule_display": "*", "state": "paused",
    },
]


def fake_resolve(ref):
    for j in JOBS:
        if j["id"] == ref:
            return dict(j)
    matches = [dict(j) for j in JOBS if j["name"].lower() == ref.lower()]
    if len(matches) > 1:
        from cron.jobs import AmbiguousJobReference
        raise AmbiguousJobReference(ref, matches)
    return matches[0] if matches else None


def ident(platform, uid):
    return UserEnvIdentity(platform=platform, user_id=uid, user_name="x",
                           user_key=f"{platform}.{uid}")


def run(label, ref, identity):
    with mock.patch.object(cp, "resolve_job_ref", fake_resolve), \
         mock.patch.object(cp, "_current_identity", lambda: identity):
        out = json.loads(cp.cron_prompt(job_id=ref))
    status = "PASS" if out.get("success") == expected[label] else "**FAIL**"
    print(f"[{status}] {label}: {json.dumps(out, ensure_ascii=False)[:160]}")
    return out


expected = {
    "1-no-identify→放行": True,
    "2-owner匹配→放行": True,
    "3-owner不匹配→拒绝": False,
    "3b-admin身份不豁免→拒绝": False,
    "4a-不存在": False,
    "4b-重名": False,
    "5-无身份读owned→拒绝": False,
}

results = {}
results["1-no-identify→放行"] = run("1-no-identify→放行", "aaa1", ident("feishu", "anyone"))
results["2-owner匹配→放行"] = run("2-owner匹配→放行", "bbb2", ident("slack", "U1"))
results["3-owner不匹配→拒绝"] = run("3-owner不匹配→拒绝", "bbb2", ident("feishu", "bcge9g7d"))
results["3b-admin身份不豁免→拒绝"] = run("3b-admin身份不豁免→拒绝", "bbb2", ident("feishu", "bcge9g7d"))
results["4a-不存在"] = run("4a-不存在", "zzz", None)
results["4b-重名"] = run("4b-重名", "dup", None)
results["5-无身份读owned→拒绝"] = run("5-无身份读owned→拒绝", "bbb2", None)

# 断言细节
r1 = results["1-no-identify→放行"]
assert r1["prompt"] == "P-NO-OWNER" and r1["owner"] is None, "用例1 prompt 全文未返回"
r2 = results["2-owner匹配→放行"]
assert r2["prompt"] == "P-SECRET" and r2["owner"] == "slack:U1", "用例2 owner 匹配未放行"
r3 = results["3-owner不匹配→拒绝"]
assert "P-SECRET" not in json.dumps(r3) and r3.get("error", "").startswith("permission denied"), "用例3 泄漏了 prompt 或错误信息不对"
assert "ambiguous" in results["4b-重名"]["error"], "用例4b 未报重名"
assert results["5-无身份读owned→拒绝"]["success"] is False

ok = all(results[k]["success"] == expected[k] for k in expected)
print("\n===", "ALL PASS" if ok else "SOME FAILED", "===")
sys.exit(0 if ok else 1)
