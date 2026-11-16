"""Built-in composer quick commands for the AISOC chat module.

AISOC has no admin database, so the equivalent of Aegis' ``system_instructs``
/ ``prompt_templates`` tables is this seed list plus an optional user
extension file (``$HERMES_HOME/aisoc_quick_commands.json``, same shape).

Each entry mirrors ``ChatQuickCommandResponse``:

    {"type": "instruct" | "prompt", "name": ..., "desc": ..., "content": ...}

``instruct`` entries are expanded server-side with a trailing newline;
``prompt`` entries are expanded verbatim (see ``QuickCommandService``).
"""

from __future__ import annotations


ONTOLOGY_SKILL_NAME = "aisoc-ontology"

# Migrated from the retired Ontology Chat page's seed system message
# (frontend useOntologyChat.ts, pre-1.0).
INSTRUCT_ONTOLOGY = f"""你是 AISOC 本体建设顾问。这个会话专门用来回答关于 AISOC 能力本体（Ontology）、扫描完整度评分、能力缺口的问题。

规则：
1. 优先使用 {ONTOLOGY_SKILL_NAME} skill 中的脚本（compile / scan / mapper / scoring / diff）来查证事实；不要凭记忆回答。
2. 每一条断言都要用 (node_id) 圆括号引用来自 mapped-graph 的证据；无证据就说"暂无直接证据"，然后建议下一步扫描/查询方向。
3. 保持回答简洁：4 段以内，中文回复。
4. 拒绝对未扫描过的域做主观评价；如果 mapper/scanner 说 partial，就说 partial。"""


# Reconstructed from the aegis html-deliverable SKILL.md contract (the original
# aegis instruct text lives only in aegis deployments' aegis.db and was not
# recoverable from this workspace). Expanded server-side with the composer's
# {theme_color} / {date} message args.
INSTRUCT_A2UI = """请把本次任务的最终结果交付为一个 HTML 文件：使用 $html-deliverable skill,按其 SKILL.md 工作流从内置模板(report / dashboard / comparison / roadmap)中选择最贴合的一个,填充真实结果后写入输出文件,并用 skill 自带的 validate_html.py 校验通过。

<theme>
{theme_color}
</theme>

要求:
1. 严格遵守 SKILL.md 的 Agent2UI Interaction Contract 与 Explicit Theme Contract;上面 <theme> 块中的颜色值原样映射到模板 CSS 变量,不得改算颜色。
2. 文档中的日期一律以 {date} 为"当前时间"基准,不要虚构时间。
3. 不要虚构数据;缺失的信息留空或明确标注"暂无数据"。
4. 最终聊天回复只做简短总结,不要提及文件路径(预览会自动打开)。"""


SEED_COMMANDS: list[dict[str, str]] = [
    {
        "type": "instruct",
        "name": "a2ui",
        "desc": "A2UI 模式：任务结果交付为可交互 HTML（html-deliverable skill + 主题跟随）",
        "content": INSTRUCT_A2UI,
    },
    {
        "type": "instruct",
        "name": "ontology",
        "desc": "本体顾问模式：基于 aisoc-ontology skill 查证回答，引用 (node_id) 证据",
        "content": INSTRUCT_ONTOLOGY,
    },
    # Migrated from the retired Ontology Chat page's QUICK_PROMPTS. The
    # consultant instructions are inlined (not a nested @[instruct_ontology]
    # token) because resolve_text intentionally never re-scans replacement
    # content for shortcut tokens.
    {
        "type": "prompt",
        "name": "ontology_completeness",
        "desc": "本体完整度：当前 completeness 分数解读与主要缺口",
        "content": f"{INSTRUCT_ONTOLOGY}\n\n当前 completeness 分数为什么是这个值？主要缺口在哪个域？",
    },
    {
        "type": "prompt",
        "name": "ontology_top3",
        "desc": "本体优先级：最应优先补齐的 Top 3 能力",
        "content": f"{INSTRUCT_ONTOLOGY}\n\n哪些能力应该最优先补齐？给出 Top 3 + 理由",
    },
    {
        "type": "prompt",
        "name": "ontology_d0",
        "desc": "本体 D0：AI 编排与知识底座满足程度",
        "content": f"{INSTRUCT_ONTOLOGY}\n\nD0 AI 编排与知识底座目前满足程度如何？",
    },
    {
        "type": "prompt",
        "name": "ontology_d5",
        "desc": "本体 D5：威胁情报域缺口扫描",
        "content": f"{INSTRUCT_ONTOLOGY}\n\n威胁情报（D5）域还缺什么？跑一次 scanner 看看",
    },
]
