from __future__ import annotations

import json
import logging
import os
import pwd
import subprocess
import threading
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


class OntologyError(RuntimeError):
    """Generic ontology error (404-worthy: missing data, unknown artifact)."""


class OntologyBusyError(RuntimeError):
    """Compile or scan is already running; returned as 409."""


class OntologyTimeoutError(RuntimeError):
    """Compile or scan exceeded its subprocess timeout; returned as 504."""


ONTOLOGY_SUBPROCESS_TIMEOUT = int(os.environ.get("AISOC_ONTOLOGY_SUBPROCESS_TIMEOUT", "300"))

_compile_lock = threading.Lock()
_scan_lock = threading.Lock()


def _real_home() -> str:
    """Return the OS-level home directory for the current uid.

    Distinct from ``Path.home()`` / ``os.environ['HOME']`` — the hermes runtime's
    ``prepare_hermes_home()`` rewrites ``HOME`` to a per-profile sandbox before
    this module is imported, which would otherwise poison ``~`` expansion in
    ``_env_path()`` defaults. ``getpwuid`` reads directly from the passwd
    database, so it survives HOME manipulation.
    """
    return pwd.getpwuid(os.getuid()).pw_dir


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


# Absolute path to the aisoc profile, resolved via the OS uid → home mapping
# rather than ~. On the canonical deploy host this is /home/amber/.hermes/…;
# on a Mac dev machine it's /Users/<login>/.hermes/…; either way it's a real
# on-disk path unaffected by any HOME env manipulation.
_PROFILE = f"{_real_home()}/.hermes/profiles/aisoc"


def _run_ontology_script(script: Path, lock: threading.Lock, label: str) -> dict[str, Any]:
    """Run an ontology skill script under a single-flight lock with timeout.

    Raises OntologyBusyError if another invocation of the same label is already
    running; OntologyTimeoutError on subprocess timeout; OntologyError with a
    generic message on any other failure (details are logged, not returned)."""
    if not lock.acquire(blocking=False):
        raise OntologyBusyError(f"{label} is already in progress")
    try:
        try:
            result = subprocess.run(
                ["python3", str(script)],
                check=True,
                capture_output=True,
                text=True,
                timeout=ONTOLOGY_SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            logger.warning("ontology %s timed out after %ss", label, ONTOLOGY_SUBPROCESS_TIMEOUT)
            raise OntologyTimeoutError(f"{label} timed out after {ONTOLOGY_SUBPROCESS_TIMEOUT}s")
        except subprocess.CalledProcessError as exc:
            logger.exception("ontology %s failed (rc=%s): %s", label, exc.returncode, exc.stderr)
            raise OntologyError(f"{label} failed")
        except FileNotFoundError as exc:
            logger.exception("ontology %s missing binary or script: %s", label, exc)
            raise OntologyError(f"{label} failed")
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            logger.exception("ontology %s produced non-JSON output", label)
            raise OntologyError(f"{label} produced malformed output")
    finally:
        lock.release()


class OntologyService:
    # v3 三层图谱是唯一真实来源。compile 只把它写到 alias 路径供旧调用点兼容。
    # 默认值使用 _PROFILE（getpwuid 拿的真 home）而不是 ~，因为 hermes runtime
    # 会在这个模块被 import 之前重写 HOME → sandbox 目录，此时 ~ 展开会得到
    # sandbox 路径，一切都错位。
    STANDARD_GRAPH_V3 = _env_path(
        "AISOC_ONTOLOGY_STANDARD_GRAPH_V3",
        f"{_PROFILE}/artifacts/ontology/standard/standard-graph_v3.json",
    )
    STANDARD_GRAPH_ALIAS = _env_path(
        "AISOC_ONTOLOGY_STANDARD_GRAPH",
        f"{_PROFILE}/artifacts/ontology/standard/standard-graph.json",
    )
    STANDARD_GRAPH = STANDARD_GRAPH_V3
    SCANS_ROOT = _env_path(
        "AISOC_ONTOLOGY_SCANS_ROOT",
        f"{_PROFILE}/artifacts/ontology/scans",
    )
    SKILL_SCRIPTS = _env_path(
        "AISOC_ONTOLOGY_SKILL_SCRIPTS",
        f"{_PROFILE}/skills/ontology/scripts",
    )

    @staticmethod
    def _read_json(path: Path) -> Any:
        if not path.exists():
            raise OntologyError(f"Ontology artifact not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _latest_scan_dir() -> Path:
        latest_file = OntologyService.SCANS_ROOT / "latest"
        if not latest_file.exists():
            raise OntologyError("No ontology scan has been generated yet")
        latest = Path(latest_file.read_text(encoding="utf-8").strip())
        if not latest.exists():
            raise OntologyError(f"Latest ontology scan path missing: {latest}")
        return latest

    @staticmethod
    def compile_standard_graph() -> dict[str, Any]:
        # v3 图是权威源；旧的 graph_compiler.py 走 model_v2，已废弃。
        # 这里只做：读 v3 → 写 alias → 返回统计信息（兼容老的 /compile 响应）。
        src = OntologyService.STANDARD_GRAPH_V3
        if not src.exists():
            raise OntologyError(f"Authoritative v3 graph missing: {src}")
        data = OntologyService._read_json(src)
        OntologyService.STANDARD_GRAPH_ALIAS.parent.mkdir(parents=True, exist_ok=True)
        OntologyService.STANDARD_GRAPH_ALIAS.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "output": str(OntologyService.STANDARD_GRAPH_ALIAS),
            "weight_total": data.get("weight_total", 100.0),
            "layer_counts": data.get("layer_counts", {}),
            "edge_stats": data.get("edge_stats", {}),
            "skipped": 0,
        }

    @staticmethod
    def run_scan() -> dict[str, Any]:
        # AI-only mainline: collect -> Hermes judgment -> deterministic guarded roll-up.
        # 单飞锁仍然保留，避免同一进程内并发 AI 扫描。
        from aisoc.backend.services.ontology_ai_service import (
            OntologyAIError,
            OntologyAIService,
        )

        if not _scan_lock.acquire(blocking=False):
            raise OntologyBusyError("scan is already in progress")
        try:
            try:
                return OntologyAIService.run_ai_scan()
            except OntologyAIError as exc:
                logger.exception("AI scan failed: %s", exc)
                raise OntologyError(f"scan failed: {exc}")
        finally:
            _scan_lock.release()

    @staticmethod
    def get_standard_graph() -> dict[str, Any]:
        # 优先读 v3；否则回退 alias（兼容尚未升级的部署）。
        if OntologyService.STANDARD_GRAPH_V3.exists():
            return OntologyService._read_json(OntologyService.STANDARD_GRAPH_V3)
        return OntologyService._read_json(OntologyService.STANDARD_GRAPH_ALIAS)

    @staticmethod
    def _recent_scans(limit: int = 5) -> list[dict[str, Any]]:
        """最近 N 次 AI 扫描的概览（scan-ai-* 目录），供 overview 上侧摘要。
        单次目录任一文件缺失就跳过，不阻断整个响应。"""
        out: list[dict[str, Any]] = []
        if not OntologyService.SCANS_ROOT.exists():
            return out
        for path in sorted(OntologyService.SCANS_ROOT.glob("scan-ai-*"), reverse=True)[:limit]:
            try:
                scorecard = OntologyService._read_json(path / "scorecard.json")
                mapped = OntologyService._read_json(path / "mapped-graph.json")
                observed_path = path / "observed-graph.json"
                generated_at = None
                if observed_path.exists():
                    observed = OntologyService._read_json(observed_path)
                    generated_at = observed.get("generated_at")
                out.append(
                    {
                        "scan_id": path.name,
                        "score": scorecard.get("completeness_score"),
                        "generated_at": generated_at,
                        "status_counts": mapped.get("status_counts", {}),
                    }
                )
            except Exception:
                continue
        return out

    @staticmethod
    def get_latest_bundle() -> dict[str, Any]:
        latest = OntologyService._latest_scan_dir()
        mapped = OntologyService._read_json(latest / "mapped-graph.json")
        scorecard = OntologyService._read_json(latest / "scorecard.json")
        gap = OntologyService._read_json(latest / "gap-report.json")
        observed = OntologyService._read_json(latest / "observed-graph.json")
        return {"latest": latest, "mapped": mapped, "scorecard": scorecard, "gap": gap, "observed": observed}

    @staticmethod
    def get_overview() -> dict[str, Any]:
        bundle = OntologyService.get_latest_bundle()
        latest = bundle["latest"]
        scorecard = bundle["scorecard"]
        mapped = bundle["mapped"]
        std = OntologyService.get_standard_graph()
        return {
            "latest_scan_id": latest.name,
            "standard_graph_path": str(OntologyService.STANDARD_GRAPH),
            "latest_scan_path": str(latest),
            "score": scorecard.get("completeness_score"),
            "status_counts": mapped.get("status_counts", {}),
            "generated_files": [
                str(OntologyService.STANDARD_GRAPH),
                str(latest / "observed-graph.json"),
                str(latest / "mapped-graph.json"),
                str(latest / "scorecard.json"),
                str(latest / "gap-report.json"),
            ],
            "standard_graph_schema": std.get("schema_version") or std.get("schema"),
            "standard_graph_counts": std.get("layer_counts", {}),
            "recent_scans": OntologyService._recent_scans(),
        }

    @staticmethod
    def get_artifact(name: str) -> Any:
        # standard graph 独立于 scan 快照 —— 前置判断避免因 scan 目录缺失导致
        # 标准图谱接口也 404
        if name == "standard":
            return OntologyService.get_standard_graph()
        bundle = OntologyService.get_latest_bundle()
        if name == "mapped":
            return bundle["mapped"]
        if name == "scorecard":
            return bundle["scorecard"]
        if name == "gap":
            return bundle["gap"]
        if name == "observed":
            return bundle["observed"]
        raise OntologyError(f"Unknown ontology artifact: {name}")

    # NOTE: legacy REST citation endpoint deleted. Ontology consulting now runs
    # through the unified chat module (instruct_ontology quick command → agent →
    # aisoc-ontology skill scripts). If a future non-LLM caller needs keyword
    # citations, resurrect ``build_chat_answer`` from git history.

    @staticmethod
    def build_roadmap() -> dict[str, Any]:
        from aisoc.backend.services.ontology_remediation_kb import get_remediation

        bundle = OntologyService.get_latest_bundle()
        mapped_nodes = bundle["mapped"].get("mapped_nodes", [])
        # 从 AI provenance 中拉每个对象的推荐建议（若存在），供 L2 汇总时穿透使用
        ai_prov: dict[str, Any] = {}
        try:
            ai_prov = OntologyService._read_json(bundle["latest"] / "ai_provenance.json")
        except Exception:
            ai_prov = {}
        object_judgments = ai_prov.get("object_judgments", {}) if isinstance(ai_prov, dict) else {}

        candidates = [n for n in mapped_nodes if n["status"] in {"partial", "missing"}]
        candidates.sort(key=lambda n: (-float(n.get("importance_weight", 0)), float(n.get("fulfillment_ratio", 0))))
        items = []
        for node in candidates[:10]:
            weight = float(node.get("importance_weight", 0))
            ratio = float(node.get("fulfillment_ratio", 0))
            status = node["status"]
            evidence = node.get("evidence", []) or []
            ev_count = len(evidence)
            # priority: missing+high-weight => immediate; else weighted gap tiers
            gap_size = weight * (1.0 - ratio)
            if status == "missing" and weight >= 2.0:
                priority = "immediate"
            elif gap_size >= 1.2 or weight >= 2.5:
                priority = "high"
            elif gap_size >= 0.5 or weight >= 1.5:
                priority = "medium"
            else:
                priority = "low"

            kb = get_remediation(node["id"]) or {}
            name = node["name_zh"]

            # 从 object_detail 里挑不达标的对象级 AI 建议（有 recommendation 且满足度未接近满分）
            ai_obj_recs: list[str] = []
            for od in node.get("object_detail", []) or []:
                js = object_judgments.get(od.get("object_id"), {})
                rec = js.get("recommendation")
                if rec and od.get("satisfaction", 0) < 0.95:
                    ai_obj_recs.append(f"{od.get('name_zh') or od.get('object_id')}: {rec}")

            # evidence-grounded status phrasing
            if status == "missing":
                status_phrase = "环境中未发现可扫描实现证据"
            elif ev_count == 0:
                status_phrase = f"已隐含存在但缺少可扫描证据（满足度 {int(ratio*100)}%）"
            else:
                status_phrase = f"已部分实现（{ev_count} 处证据，满足度 {int(ratio*100)}%）"

            # D5 情报能力有个特化话术：接近满分时强调"直接实现证据未沉淀"
            if node["domain"] == "D5" and ratio >= 0.9:
                status_phrase = f"情报能力已基本具备，当前主要缺少直接实现证据沉淀（满足度 {int(ratio*100)}%）"

            gap = kb.get("gap") or f"{name} {status_phrase}，尚未标准化为可映射、可评分对象。"
            generic_action = kb.get("action") or f"补齐 {name} 的可扫描实现证据与标准属性，建立到相邻能力的关系，使其可映射、可评分。"
            # 有 ≥2 条对象级 AI 建议时优先用具体建议，避免通用 boilerplate
            if ai_obj_recs and len(ai_obj_recs) >= 2:
                action = "优先按下列 L3 对象级建议补齐直接证据与调用链，避免重复建设。"
            else:
                action = generic_action
            assets = kb.get("assets", "")
            impact = kb.get("impact", "")
            effort = kb.get("effort", "M")

            # sample evidence paths (dedup, top 3) so recommendation is grounded
            ev_paths = []
            seen = set()
            for e in evidence:
                p = e.get("source_path")
                if p and p not in seen:
                    seen.add(p)
                    ev_paths.append(p)
                if len(ev_paths) >= 3:
                    break

            # composite human-readable recommendation
            rec_parts = [f"【现状】{status_phrase}。", f"【缺口】{gap}", f"【建议】{action}"]
            if ai_obj_recs:
                rec_parts.append(f"【AI对象级建议】{'；'.join(ai_obj_recs[:4])}")
            if assets:
                rec_parts.append(f"【涉及数据源/集成】{assets}。")
            if impact:
                rec_parts.append(f"【影响】{impact}")
            recommendation = " ".join(rec_parts)

            items.append(
                {
                    "node_id": node["id"],
                    "title": name,
                    "domain": node["domain"],
                    "status": status,
                    "importance_weight": node["importance_weight"],
                    "fulfillment_ratio": node["fulfillment_ratio"],
                    "priority": priority,
                    "gap_score": round(gap_size, 2),
                    "evidence_count": ev_count,
                    "gap": gap,
                    "action": action,
                    "assets": assets,
                    "impact": impact,
                    "effort": effort,
                    "evidence_samples": ev_paths,
                    "rationale": f"权重 {node['importance_weight']} × 缺口 {round(1.0 - ratio, 2)} = 加权缺口 {round(gap_size, 2)}，优先级 {priority}。",
                    "recommendation": recommendation,
                    "ai_object_recommendations": ai_obj_recs[:4],
                }
            )
        return {"items": items}

