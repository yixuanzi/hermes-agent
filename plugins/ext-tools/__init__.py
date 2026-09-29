"""ext-tools — 无状态扩展工具宿主插件。

承载后续潜在的无状态（只读、无副作用）Tools 需求，避免扩充核心工具面。
一个工具一个模块文件，在本文件 register() 中统一注册。

已提供工具：
  - cron_prompt : 取回计划任务全量 prompt（带 Owner 身份严格校验，admin 不豁免）

已提供命令：
  - /userenv : 用户自管的托管环境变量（不经 LLM）
  - /codex   : 当前会话切换到本地 codex app-server 执行（会话级，不改配置）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import codex_cmd as _codex_cmd  # noqa: E402
import cron_prompt as _cron_prompt  # noqa: E402
import userenv_cmd as _userenv_cmd  # noqa: E402


def register(ctx):
    _cron_prompt.register(ctx)
    _userenv_cmd.register(ctx)
    _codex_cmd.register(ctx)
