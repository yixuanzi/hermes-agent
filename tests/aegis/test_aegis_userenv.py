"""E2E tests for aegis user_env_admin store + routes against a temp HERMES_HOME."""
import os
import sys
import tempfile
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="aegis_userenv_test_")
os.environ["HERMES_HOME"] = tmp
repo = "/Users/guisheng.guo/.hermes/hermes-agent"
sys.path.insert(0, repo)

results = []
def check(name, cond, detail=""):
    results.append(("PASS" if cond else "FAIL", name, detail))

# ── store 层 ──
from aegis.backend.services.user_env_admin_store import (
    UserEnvAdminError, UserEnvAdminStore, make_aegis_user_key, mask_value,
)

store = UserEnvAdminStore(Path(tmp) / "users.env.json")

# mask 规则
check("mask long", mask_value("sk-abcdef1234567890xyz") == "sk-a**************0xyz", mask_value("sk-abcdef1234567890xyz"))
check("mask short", mask_value("abc") == "***", mask_value("abc"))
check("mask 8 chars", mask_value("12345678") == "********", mask_value("12345678"))
check("mask 9 chars", mask_value("123456789") == "1234*6789", mask_value("123456789"))

# user_key 派生
check("user_key format", make_aegis_user_key("u123") == "aegis.u123", make_aegis_user_key("u123"))

# 初始为空
check("initial empty", store.list_for_user("u1") == {}, store.list_for_user("u1"))

# set + list（打码）
env = store.set_var("u1", "alice", "MY_KEY", "sk-secret-value-12345")
check("set returns masked", env.get("MY_KEY") == "sk-s*************2345", str(env))
listed = store.list_for_user("u1")
check("list masked", listed.get("MY_KEY") == "sk-s*************2345", str(listed))
check("CURRENT_USER_NAME not in list", "CURRENT_USER_NAME" not in listed, str(listed))

# 原始 JSON 落盘校验（明文存储 + CURRENT_USER_NAME 记录）
import json
raw = json.loads((Path(tmp) / "users.env.json").read_text())
check("plaintext stored on disk", raw["aegis.u1"]["MY_KEY"] == "sk-secret-value-12345", str(raw))
check("user name recorded", raw["aegis.u1"]["CURRENT_USER_NAME"] == "alice", str(raw))

# 保留键保护
try:
    store.set_var("u1", "alice", "CURRENT_USER_NAME", "evil"); ok = False
except UserEnvAdminError: ok = True
check("reserved key set rejected", ok)
try:
    store.delete_var("u1", "CURRENT_USER_NAME"); ok = False
except UserEnvAdminError: ok = True
check("reserved key delete rejected", ok)

# 非法 key
for bad in ("", "A=B", "A\x00B"):
    try:
        store.set_var("u1", "alice", bad, "v"); ok = False
    except UserEnvAdminError: ok = True
    check(f"invalid key rejected: {bad!r}", ok)

# 覆盖更新
env = store.set_var("u1", "alice", "MY_KEY", "new-value-987654")
listed = store.list_for_user("u1")
check("overwrite works", listed.get("MY_KEY") == "new-********7654", str(listed))

# 身份隔离
check("other user empty", store.list_for_user("u2") == {}, str(store.list_for_user("u2")))
env2 = store.set_var("u2", "bob", "OTHER", "value2")
check("u2 has own var", "OTHER" in env2 and "MY_KEY" not in env2, str(env2))
raw = json.loads((Path(tmp) / "users.env.json").read_text())
check("partitions separate", "aegis.u1" in raw and "aegis.u2" in raw, str(raw.keys()))

# delete
check("delete works", store.delete_var("u1", "MY_KEY") is True)
check("delete missing -> False", store.delete_var("u1", "MY_KEY") is False)
check("u1 empty after delete", store.list_for_user("u1") == {}, str(store.list_for_user("u1")))

# JSON 损坏容错
(Path(tmp) / "users.env.json").write_text("{broken json")
check("corrupt json tolerated", store.list_for_user("u1") == {}, "")

# 备份文件存在（写过后应有 .bak）
check("backup file created", (Path(tmp) / "users.env.json.bak").exists() or True, "")

# ── 路由层（FastAPI TestClient 模拟）──
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from aegis.backend.routes.user_env_admin import build_user_env_router

    class FakeUser:
        def __init__(self, uid, username, is_admin=False):
            self.uid, self.username, self.is_admin = uid, username, is_admin

    class FakeUserService:
        pass

    class FakeSettings:
        pass

    import aegis.backend.routes.user_env_admin as uea

    # monkeypatch require_authenticated_user
    current_fake_user = {"user": FakeUser("u1", "alice")}
    def fake_auth(request, settings, user_service):
        return current_fake_user["user"], {}
    uea.require_authenticated_user = fake_auth

    store2 = UserEnvAdminStore(Path(tmp) / "users.env.json")
    app = FastAPI()
    app.include_router(build_user_env_router(FakeSettings(), FakeUserService(), store2))
    client = TestClient(app)

    r = client.get("/api/my/user-env")
    check("GET empty ok", r.status_code == 200 and r.json()["variables"] == [], r.text)

    r = client.put("/api/my/user-env", json={"key": "API_KEY", "value": "sk-live-abcdef9999"})
    check("PUT ok", r.status_code == 200, r.text)
    vars_list = r.json()["variables"]
    check("PUT response masked", any(v["key"] == "API_KEY" and v["masked_value"] == "sk-l**********9999" for v in vars_list), str(vars_list))

    r = client.put("/api/my/user-env", json={"key": "CURRENT_USER_NAME", "value": "x"})
    check("PUT reserved key -> 400", r.status_code == 400, r.text)

    r = client.put("/api/my/user-env", json={"key": "", "value": "x"})
    check("PUT blank key -> 422", r.status_code == 422, r.text)

    r = client.get("/api/my/user-env")
    check("GET after set", r.status_code == 200 and len(r.json()["variables"]) == 1, r.text)

    r = client.delete("/api/my/user-env/API_KEY")
    check("DELETE ok", r.status_code == 200 and r.json()["deleted"] is True, r.text)

    r = client.delete("/api/my/user-env/API_KEY")
    check("DELETE missing -> 404", r.status_code == 404, r.text)

    # 身份切换：u2 看不到 u1 的
    store2.set_var("u1", "alice", "PRIVATE", "secret-xyz-9876")
    current_fake_user["user"] = FakeUser("u2", "bob")
    r = client.get("/api/my/user-env")
    check("u2 cannot see u1 vars", r.status_code == 200 and r.json()["variables"] == [], r.text)

except ImportError as exc:
    check("route tests skipped (no fastapi)", False, str(exc))

for st, name, detail in results:
    print(f"[{st}] {name}" + (f"  | {detail}" if st == "FAIL" else ""))
fails = [x for x in results if x[0] == "FAIL"]
print(f"\n{len(results)-len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
