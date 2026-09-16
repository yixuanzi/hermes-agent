import os, sys, tempfile

tmp = tempfile.mkdtemp(prefix="hermes_userenv_test_")
os.environ["HERMES_HOME"] = tmp
repo = "/Users/guisheng.guo/.hermes/hermes-agent"
sys.path.insert(0, repo)

from tools.user_env_runtime import set_current_user_env_identity, reset_current_user_env_identity

sys.path.insert(0, f"{repo}/plugins/ext-tools")
import userenv_cmd

results = []
def check(name, cond, detail=""):
    results.append(("PASS" if cond else "FAIL", name, detail))

# 清除 subprocess-env 桥继承的真实会话身份，以便测试"无身份"分支
for _k in ("HERMES_SESSION_PLATFORM", "HERMES_SESSION_USER_ID", "HERMES_SESSION_USER_NAME"):
    os.environ.pop(_k, None)

# 1. 无身份 -> fail-closed
r = userenv_cmd.userenv_command("list")
check("no-identity rejected", "无法识别" in r, r)

# 2. 用户 A
tok = set_current_user_env_identity("feishu", "userA", "Alice")
r = userenv_cmd.userenv_command("list")
check("A empty list", "暂无" in r, r)
r = userenv_cmd.userenv_command("set MY_KEY sk-abcdef1234567890xyz")
check("A set ok", "已设置" in r, r)
r = userenv_cmd.userenv_command("list")
check("A list shows key", "MY_KEY" in r, r)
r = userenv_cmd.userenv_command("get MY_KEY")
check("A get masked", "sk-a" in r and "0xyz" in r and "abcdef" not in r and "1234567" not in r, r)
userenv_cmd.userenv_command("set SPACED hello world 123")
r = userenv_cmd.userenv_command("get SPACED")
check("spaced value stored", "hell" in r and "123" in r, r)
userenv_cmd.userenv_command("set SHORT abc")
r = userenv_cmd.userenv_command("get SHORT")
check("short value fully masked", "*" in r and "abc" not in r.replace("*", ""), r)

# 3. 隔离
tok_b = set_current_user_env_identity("feishu", "userB", "Bob")
r = userenv_cmd.userenv_command("list")
check("B cannot see A keys", "MY_KEY" not in r, r)
r = userenv_cmd.userenv_command("get MY_KEY")
check("B get denied", "不存在" in r, r)
reset_current_user_env_identity(tok_b)

# 4. delete
r = userenv_cmd.userenv_command("delete MY_KEY")
check("A delete ok", "已删除" in r, r)
r = userenv_cmd.userenv_command("get MY_KEY")
check("A get after delete", "不存在" in r, r)
r = userenv_cmd.userenv_command("delete NOPE")
check("delete missing key", "不存在" in r, r)
reset_current_user_env_identity(tok)

# 5. help / unknown
r = userenv_cmd.userenv_command("")
check("help on empty", "/userenv" in r and "list" in r, r[:80])
tok = set_current_user_env_identity("feishu", "userA", "Alice")
r = userenv_cmd.userenv_command("frobnicate x")
check("unknown sub", "未知子命令" in r, r)
reset_current_user_env_identity(tok)

# 6/7. 注册接口冒烟
class FakeCtx:
    def __init__(self): self.cmds = {}; self.tools = []
    def register_command(self, name, handler, description="", args_hint=""):
        self.cmds[name] = (handler, description, args_hint)
    def register_tool(self, name, toolset="", schema=None, handler=None, **kw):
        self.tools.append(name)

ctx = FakeCtx()
userenv_cmd.register(ctx)
check("register_command called", "userenv" in ctx.cmds, str(ctx.cmds.keys()))

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "ext_tools_under_test", f"{repo}/plugins/ext-tools/__init__.py"
)
ext_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ext_tools)
fctx = FakeCtx()
ext_tools.register(fctx)
check("plugin package register", "userenv" in fctx.cmds, str(fctx.cmds.keys()))

# 8. gateway/run.py 语法校验
import py_compile
py_compile.compile(f"{repo}/gateway/run.py", doraise=True)
check("gateway/run.py compiles", True)

for st, name, detail in results:
    print(f"[{st}] {name}" + (f"  | {detail}" if st == "FAIL" else ""))
fails = [x for x in results if x[0] == "FAIL"]
print(f"\n{len(results)-len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
