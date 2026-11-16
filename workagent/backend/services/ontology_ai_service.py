from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from importlib import util
from pathlib import Path
from typing import Any, Callable, Optional

from workagent.backend.services.ontology_service import (
    OntologyService,
    _PROFILE,
    _real_home,
)


logger = logging.getLogger(__name__)


class OntologyAIError(RuntimeError):
    pass


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


AI_HERMES_BIN = os.environ.get("WORKAGENT_ONTOLOGY_HERMES_BIN", "hermes")
AI_PROFILE = os.environ.get("WORKAGENT_ONTOLOGY_PROFILE", "workagent")
AI_SUBPROCESS_TIMEOUT = int(os.environ.get("WORKAGENT_ONTOLOGY_AI_TIMEOUT", "600"))
AI_JUDGE_BATCH_SIZE = int(os.environ.get("WORKAGENT_ONTOLOGY_AI_BATCH_SIZE", "8"))


class OntologyAIService:
    """AI-driven diff analysis: collect -> Hermes judgment -> scored roll-up.

    Uses SKILL_SCRIPTS from OntologyService so path env-vars stay in one place.
    """

    PROMPT_HEADER = (
        "你是WORKAGENT Ontology AI差异分析判定器。"
        "你将看到对象 definition/support/implementation 和真实候选证据 candidate_evidence。"
        "请按实现成熟度做语义判定，而不是只因证据不是直接源码就判 0。"
        "skill目录、配置路径、知识库目录、datasource inventory 命中都可以作为有效证据，但若仅能证明存在性而不能证明成熟实现，应给 partial 而不是 satisfied。"
        "输出严格JSON，不要markdown，不要额外说明。"
        "输出格式:{\"judgments\":[{\"object_id\":\"...\",\"satisfaction\":0.0,"
        "\"confidence\":\"high|medium|low\",\"verdict\":\"satisfied|partial|missing|unverifiable\","
        "\"evidence_refs\":[\"...\"],\"reason\":\"...\",\"recommendation\":\"...\"}]}。"
        "规则：evidence_refs 只能从输入 candidate_evidence.source_path 中选择；"
        "至少引用 1 条有效 evidence_ref，除非确实没有候选证据；"
        "若有候选证据但只能证明基础存在/配置接入/分散能力，应给 0.3~0.8 的 partial；"
        "只有当证据足以支持实现成熟且与 implementation 高度一致时才给 >=0.95 satisfied；"
        "对 partial/missing/unverifiable 必须给具体可执行优化建议 recommendation。"
    )

    @staticmethod
    def _skill_scripts() -> Path:
        return OntologyService.SKILL_SCRIPTS

    @staticmethod
    def _ontology_path_overrides() -> dict[str, str]:
        """WORKAGENT_ONTOLOGY_* → absolute-path overrides that make ``_paths.py``
        (imported by scanner_ai.py, whether run as a subprocess or exec'd
        in-process) resolve correctly no matter what the current HOME env var
        happens to be.

        The hermes runtime rewrites HOME to a per-profile sandbox at points
        that aren't limited to "when it forks a child process" — it can
        happen to *this* backend process's own env too (we've seen it land on
        an unrelated profile like search_agent). ``_paths.py``'s ``_resolve()``
        checks ``os.environ.get(env_name, ...)`` before ever falling back to
        ``Path.home()``, so setting these explicitly makes it immune to
        whatever HOME currently is. Deliberately excludes HOME itself — see
        callers for why we don't touch that.
        """
        home_dir = _real_home()
        return {
            "WORKAGENT_ONTOLOGY_PROFILE": _PROFILE,
            "WORKAGENT_ONTOLOGY_STANDARD_GRAPH": str(OntologyService.STANDARD_GRAPH_ALIAS),
            "WORKAGENT_ONTOLOGY_STANDARD_GRAPH_V3": str(OntologyService.STANDARD_GRAPH_V3),
            "WORKAGENT_ONTOLOGY_SCANS_ROOT": str(OntologyService.SCANS_ROOT),
            "WORKAGENT_ONTOLOGY_SKILL_SCRIPTS": str(OntologyService.SKILL_SCRIPTS),
            "WORKAGENT_ONTOLOGY_WIKI": f"{home_dir}/workagentwiki",
            "WORKAGENT_ONTOLOGY_WORKSPACE": f"{home_dir}/workspace",
            "WORKAGENT_ONTOLOGY_REPORTS": f"{home_dir}/reports",
        }

    @staticmethod
    def _subprocess_env() -> dict[str, str]:
        """Env passed to skill-script subprocesses (collect()). Builds on
        ``_ontology_path_overrides()`` and additionally pins HOME — safe here
        because it's a *child process's* env, not this process's, so it can't
        disturb whatever sandboxed HOME the hermes runtime wants in effect for
        unrelated in-process work happening concurrently in this backend.
        """
        env = os.environ.copy()
        env["HOME"] = _real_home()
        env.update(OntologyAIService._ontology_path_overrides())
        return env

    @staticmethod
    def _run(cmd: list[str]) -> dict[str, Any]:
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=AI_SUBPROCESS_TIMEOUT,
                env=OntologyAIService._subprocess_env(),
            )
        except subprocess.TimeoutExpired:
            raise OntologyAIError(f"AI subprocess timed out after {AI_SUBPROCESS_TIMEOUT}s: {cmd[0]}")
        except subprocess.CalledProcessError as exc:
            logger.exception("AI subprocess failed (rc=%s): %s", exc.returncode, exc.stderr)
            raise OntologyAIError(f"AI subprocess failed: {exc.stderr[:2400] if exc.stderr else exc}")
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            raise OntologyAIError(f"AI subprocess produced non-JSON: {result.stdout[:400]}")

    @staticmethod
    def collect() -> dict[str, Any]:
        # Pass STANDARD_GRAPH_V3 as an explicit positional arg so the subprocess
        # doesn't rely on env resolution at all — this makes it work even when
        # the sandbox HOME rewrite gets ahead of our env override.
        cmd = [
            "python3",
            str(OntologyAIService._skill_scripts() / "scanner_ai.py"),
            "collect",
            str(OntologyService.STANDARD_GRAPH_V3),
        ]
        return OntologyAIService._run(cmd)

    @staticmethod
    def _call_hermes(prompt: str) -> str:
        cmd = [AI_HERMES_BIN, "-z", prompt, "--profile", AI_PROFILE, "--yolo"]
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=AI_SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise OntologyAIError(f"Hermes CLI timed out after {AI_SUBPROCESS_TIMEOUT}s")
        except FileNotFoundError:
            raise OntologyAIError(f"Hermes CLI not found: {AI_HERMES_BIN}")
        except subprocess.CalledProcessError as exc:
            logger.exception("Hermes CLI failed (rc=%s): %s", exc.returncode, exc.stderr)
            raise OntologyAIError(f"Hermes CLI failed: {exc.stderr[:2400] if exc.stderr else exc}")
        return result.stdout.strip()

    @staticmethod
    def _sanitize_judgment_items(items: list[dict[str, Any]], batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed: dict[str, set[str]] = {}
        for obj in batch:
            refs = [e.get("source_path") for e in obj.get("candidate_evidence", []) if e.get("source_path")]
            allowed[obj.get("object_id")] = set(refs)
        out: list[dict[str, Any]] = []
        for item in items:
            oid = item.get("object_id")
            refs = [r for r in (item.get("evidence_refs") or []) if r in allowed.get(oid, set())]
            sat = float(item.get("satisfaction", 0.0) or 0.0)
            if not refs:
                sat = 0.0
                verdict = "unverifiable"
            else:
                verdict = item.get("verdict") or (
                    "satisfied" if sat >= 0.95 else ("partial" if sat > 0 else "missing")
                )
            out.append(
                {
                    "object_id": oid,
                    "satisfaction": max(0.0, min(1.0, sat)),
                    "confidence": item.get("confidence") or "medium",
                    "verdict": verdict,
                    "evidence_refs": refs,
                    "reason": item.get("reason", ""),
                    "recommendation": item.get("recommendation", ""),
                }
            )
        return out

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                raise OntologyAIError(f"AI output is not valid JSON: {text[:500]}")
            return json.loads(m.group(0))

    @staticmethod
    def judge(
        bundle_path: str,
        batch_size: int = AI_JUDGE_BATCH_SIZE,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> str:
        skill_scripts = OntologyAIService._skill_scripts()
        baseline_path = skill_scripts.parent / "references" / "ai_judgments_baseline_v3.json"
        baseline_by_oid: dict[str, dict[str, Any]] = {}
        if baseline_path.exists():
            try:
                baseline_raw = json.loads(baseline_path.read_text(encoding="utf-8"))
                baseline_by_oid = {
                    j.get("object_id"): j
                    for j in baseline_raw.get("judgments", [])
                    if j.get("object_id")
                }
            except Exception:
                baseline_by_oid = {}
        bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
        objects = bundle.get("objects", [])
        judgments: list[dict[str, Any]] = []
        total_batches = max(1, (len(objects) + batch_size - 1) // batch_size)
        for batch_index, i in enumerate(range(0, len(objects), batch_size), start=1):
            batch = objects[i : i + batch_size]
            if progress_cb:
                try:
                    progress_cb(batch_index, total_batches)
                except Exception:
                    logger.exception("progress_cb raised; continuing")
            baseline_subset = [
                baseline_by_oid[o.get("object_id")]
                for o in batch
                if o.get("object_id") in baseline_by_oid
            ]
            prompt = (
                OntologyAIService.PROMPT_HEADER
                + "\n参考历史判定(仅作校准，不可照抄，必须以本次 candidate_evidence 为准):\n"
                + json.dumps(baseline_subset, ensure_ascii=False)
                + "\n对象数据:\n"
                + json.dumps(batch, ensure_ascii=False)
            )
            items: Optional[list[dict[str, Any]]] = None
            last_err: Optional[Exception] = None
            for attempt in range(3):
                try:
                    run_prompt = (
                        prompt
                        if attempt == 0
                        else (
                            prompt
                            + "\n上一次输出不是合法 JSON。请仅输出严格合法的 JSON，禁止任何解释、markdown、前后缀。"
                        )
                    )
                    raw = OntologyAIService._call_hermes(run_prompt)
                    parsed = OntologyAIService._extract_json(raw)
                    items = parsed.get("judgments", [])
                    if not isinstance(items, list):
                        raise OntologyAIError("AI judgments payload missing judgments list")
                    break
                except Exception as exc:
                    last_err = exc
                    items = None
            if items is None:
                if baseline_subset and len(baseline_subset) == len(batch):
                    items = baseline_subset
                else:
                    raise OntologyAIError(f"AI batch judgment failed after retries: {last_err}")
            judgments.extend(OntologyAIService._sanitize_judgment_items(items, batch))
        out = {
            "engine": "llm",
            "judge": "Hermes WORKAGENT runtime",
            "prompt_version": "ai-diff-p1-runtime",
            "judgments": judgments,
        }
        out_path = Path(bundle_path).with_name("judgments.runtime.json")
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(out_path)

    @staticmethod
    def score(judgments_path: str, promote_latest: bool = True) -> dict[str, Any]:
        mod_path = OntologyAIService._skill_scripts() / "scanner_ai.py"
        spec = util.spec_from_file_location("scanner_ai_runtime", mod_path)
        if spec is None or spec.loader is None:
            raise OntologyAIError(f"Failed loading scanner_ai module: {mod_path}")
        module = util.module_from_spec(spec)
        # Unlike collect()/judge(), this runs scanner_ai.py IN-PROCESS via
        # importlib (not as a subprocess), so _subprocess_env()'s child-only
        # env dict never applies here — exec_module reads THIS process's
        # ambient os.environ directly, which the hermes runtime may have
        # rewritten HOME on at any point. Temporarily set the ontology path
        # overrides on our own env for the duration of the exec (not HOME
        # itself — see _ontology_path_overrides()'s docstring), then restore
        # whatever was there before so we don't leak overrides into unrelated
        # code running concurrently in other threads of this backend.
        overrides = OntologyAIService._ontology_path_overrides()
        saved = {k: os.environ.get(k) for k in overrides}
        os.environ.update(overrides)
        try:
            spec.loader.exec_module(module)
            return module.score(judgments_path, module.STANDARD_DEFAULT, promote_latest=promote_latest)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    @staticmethod
    def run_ai_scan(progress_cb: Optional[Callable[[int, int], None]] = None) -> dict[str, Any]:
        collect_result = OntologyAIService.collect()
        bundle_path = str(Path(collect_result["output_dir"]) / "evidence-bundle.json")
        judgments_path = OntologyAIService.judge(bundle_path, progress_cb=progress_cb)
        score_result = OntologyAIService.score(judgments_path, promote_latest=True)
        score_outdir = Path(score_result["output_dir"])
        try:
            score_outdir.mkdir(parents=True, exist_ok=True)
            score_outdir.joinpath("judgments.runtime.json").write_text(
                Path(judgments_path).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("could not persist runtime judgments alongside scan")
        return {
            "scan_id": score_result["scan_id"],
            "output_dir": score_result["output_dir"],
            "score": score_result["completeness_score"],
            "engine": "ai",
            "status_counts": score_result.get("status_counts", {}),
            "collect_scan_id": collect_result["scan_id"],
            "bundle_path": bundle_path,
            "judgments_path": judgments_path,
            "guard_drops": score_result.get("guard_drops", 0),
        }
