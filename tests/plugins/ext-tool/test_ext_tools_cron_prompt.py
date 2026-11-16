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

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plugins" / "ext-tools"))

import cron_prompt as cp
from tools.user_env_runtime import UserEnvIdentity

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


def run(ref, identity):
    with mock.patch.object(cp, "resolve_job_ref", fake_resolve), \
         mock.patch.object(cp, "_current_identity", lambda: identity):
        return json.loads(cp.cron_prompt(job_id=ref))


def test_no_identify_returns_full_prompt():
    out = run("aaa1", ident("feishu", "anyone"))
    assert out["success"] is True
    assert out["prompt"] == "P-NO-OWNER" and out["owner"] is None


def test_matching_owner_allowed():
    out = run("bbb2", ident("slack", "U1"))
    assert out["success"] is True
    assert out["prompt"] == "P-SECRET" and out["owner"] == "slack:U1"


@pytest.mark.parametrize("uid", ["bcge9g7d", "admin"])
def test_other_caller_denied_even_if_admin(uid):
    out = run("bbb2", ident("feishu", uid))
    assert out["success"] is False
    assert "P-SECRET" not in json.dumps(out)
    assert out.get("error", "").startswith("permission denied")


def test_missing_job():
    assert run("zzz", None)["success"] is False


def test_duplicate_name_is_ambiguous():
    out = run("dup", None)
    assert out["success"] is False
    assert "ambiguous" in out["error"]


def test_no_identity_denied_for_owned_job():
    out = run("bbb2", None)
    assert out["success"] is False
    assert "P-SECRET" not in json.dumps(out)
