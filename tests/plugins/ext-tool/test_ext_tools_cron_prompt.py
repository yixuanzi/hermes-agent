"""ext-tools / cron_prompt 验证测试（只读，不修改真实数据）。

可见性走 core 的 tools.cronjob_visibility（与 cronjob 工具同一套规则），这里只替换任务存储
查询与绑定的调用者身份：
  1. 无 identify 的任务 → 直接返回全量 prompt
  2. 有 identify 且 Owner 匹配 → 放行
  3. 有 identify 但调用者不匹配 → 当作不存在（即使调用者是"管理员"——本工具无角色豁免），
     不回显 prompt，也不透露任务归谁
  4. 任务不存在 / 重名 → 明确报错
  5. 无身份（CLI 等场景）读带 identify 的任务 → 当作不存在
"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[3]

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
    import tools.cronjob_visibility as visibility

    with mock.patch.object(visibility, "resolve_job_ref", fake_resolve), \
         mock.patch.object(visibility, "caller_identity", lambda: identity):
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
    assert out["error"] == "cron job not found: bbb2"
    assert "slack:U1" not in json.dumps(out)  # 也不透露任务归谁


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
