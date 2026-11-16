"""Per-subcapability remediation knowledge base for AISOC Ontology v3 (3-layer).

Keyed by Layer-2 subcapability id (e.g. D1.1). The roadmap builder blends these
with live scan facts (status, fulfillment_ratio, unmet objects, evidence paths)
to produce grounded, non-generic recommendations. Missing keys fall back to a
generic template driven by the subcapability name + unmet object support text.

Fields: gap / action / assets / impact / effort(S|M|L)
"""
from __future__ import annotations

REMEDIATION_KB: dict[str, dict[str, str]] = {
    "D0.1": {"gap": "Agent 编排已具备，但任务调度与 DAG 编排缺少统一可扫描证据。",
             "action": "将 cron/任务调度与子任务派发抽象为统一调度对象，暴露调度状态与审计。",
             "assets": "cron/jobs.json、任务调度器、运行监控", "impact": "调度不可观测会导致自动化黑盒、失败难追踪。", "effort": "M"},
    "D0.4": {"gap": "知识库/RAG 存在雏形，但会话历史与检索引用尚未标准化为可扫描对象。",
             "action": "统一知识源索引与证据引用规范，暴露检索接口，落地防幻觉引用链。",
             "assets": "aisocwiki、session history、检索索引", "impact": "缺失时 AI 回答难以引用证据，防幻觉能力打折。", "effort": "M"},
    "D1.1": {"gap": "多源数据接入完善，但云审计(云控制面)接入证据不足。",
             "action": "补齐云审计日志(CloudTrail/云平台)接入连接器并纳入数据源清单。",
             "assets": "云审计日志、只读 API 凭据、datasource inventory", "impact": "云侧盲区会削弱云原生威胁检测。", "effort": "M"},
    "D1.4": {"gap": "身份账户测绘部分满足，目录/IdP 与账户目录缺少统一对象。",
             "action": "接入 AD/LDAP/IdP 账户目录，建模 identity→account→privilege 链。",
             "assets": "目录服务、IAM/AD 事件、账户属性同步", "impact": "身份链不完整会削弱账户盗用与越权分析。", "effort": "M"},
    "D2.5": {"gap": "UEBA/行为分析仅有规则级信号，缺少独立行为基线特征对象。",
             "action": "沉淀 UEBA 特征与基线数据源，建立异常评分模型对象。",
             "assets": "行为遥测、特征工程管道", "impact": "无基线则异常检测依赖静态阈值，漏报率高。", "effort": "L"},
    "D5.1": {"gap": "IOC 情报查询依赖网关配置，缺少独立情报库与查询工具对象化证据。",
             "action": "将 TIP 查询能力对象化，落地情报库缓存与批量查询配额管理。",
             "assets": "TIP、情报库、查询 API", "impact": "情报能力不可观测会影响富化质量与配额治理。", "effort": "S"},
    "D5.2": {"gap": "情报生产与富化缺少标准化情报库与富化动作证据。",
             "action": "建立情报归一化入库与评分/标签化管道。",
             "assets": "内外部情报源、归一化管道", "impact": "缺失时富化不可复用、难以审计。", "effort": "M"},
    "D5.5": {"gap": "链上/合约情报为可选扩展，当前仅有配置级信号。",
             "action": "如业务需要，接入区块链浏览器 API 与合约分析引擎；否则标记为可选。",
             "assets": "区块链浏览器、合约分析引擎", "impact": "非通用能力，按业务取舍。", "effort": "S"},
    "D7.3": {"gap": "SOAR 编排存在网关能力，但编排动作与 playbook 库未完全对象化。",
             "action": "沉淀 SOAR playbook 库与执行凭据，暴露 incident/execution 状态。",
             "assets": "SOAR 平台、playbook 库、审计日志", "impact": "编排不可观测会削弱自动化响应可靠性。", "effort": "M"},
    "D8.2": {"gap": "AD 域攻击模拟已具备，AD 事件日志对象化证据可加强。",
             "action": "确认 wineventlog/adi 索引接入完整，用于攻击模拟检测验证。",
             "assets": "wineventlog、adi 索引", "impact": "缺失会导致模拟无法验证检测覆盖。", "effort": "S"},
    "D9.3": {"gap": "报告投递依赖协作平台配置，投递动作未完全对象化。",
             "action": "统一报告投递通道与导出渲染，暴露投递结果验证。",
             "assets": "协作平台通道、导出渲染", "impact": "投递不可靠会影响运营闭环。", "effort": "S"},
    "D9.4": {"gap": "知识库运营检索动作缺少标准化对象证据。",
             "action": "标准化知识检索与沉淀流程，纳入知识索引。",
             "assets": "aisocwiki、检索索引", "impact": "知识沉淀不规范会降低复用效率。", "effort": "S"},
}


def get_remediation(node_id: str) -> dict[str, str] | None:
    return REMEDIATION_KB.get(node_id)
