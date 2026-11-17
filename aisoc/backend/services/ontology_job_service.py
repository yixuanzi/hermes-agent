from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Optional


from aisoc.backend.services.ontology_ai_service import (
    OntologyAIError,
    OntologyAIService,
)
from aisoc.backend.services.ontology_service import (
    OntologyError,
    _ProfilePath,
)


logger = logging.getLogger(__name__)

# A job with no progress update for longer than this is treated as dead (e.g.
# the backend process restarted mid-scan, killing the daemon thread that was
# advancing it) rather than "still running" — otherwise a crashed scan would
# show a permanently-stuck progress bar to every client that asks.
STALE_JOB_SECONDS = 20 * 60

# Startup-reap grace window: shorter than STALE_JOB_SECONDS on purpose. This
# process just booted, so its own _active_job_id is necessarily None — any
# job still marked queued/running is either (a) truly orphaned by a *previous*
# incarnation of this backend that died mid-scan, or (b) being actively
# advanced right now by a *different, still-alive* process that happens to
# share this JOB_ROOT (two backend processes on different ports launched with
# the same hermes profile legitimately see the same job files). A
# job in category (b) will have a very recent updated_at (each collect/judge
# batch/score step writes progress every few seconds to tens of seconds), so
# a short grace window distinguishes the two without needing STALE_JOB_SECONDS'
# full 20 minutes.
STARTUP_REAP_GRACE_SECONDS = 45


class OntologyJobService:
    """Async wrapper around AI scan: create_scan_job returns immediately with a
    job_id; a daemon thread advances the job through collect -> judge -> score
    stages and writes progress to a JSON file the client polls."""

    JOB_ROOT = _ProfilePath("AISOC_ONTOLOGY_JOBS_ROOT", "artifacts", "ontology", "jobs")

    # In-memory pointer to the currently in-flight job, guarded by _lock. Lets
    # create_scan_job() dedupe concurrent "Run Scan" clicks (e.g. a stale
    # frontend tab that doesn't know a scan is already running) into the same
    # job instead of spawning a second one, and lets GET /scan/active answer
    # "is anything running" without the caller needing to already know a
    # job_id — which is exactly what a freshly (re)mounted page needs.
    _lock = Lock()
    _active_job_id: Optional[str] = None

    @staticmethod
    def _job_path(job_id: str) -> Path:
        return OntologyJobService.JOB_ROOT / f"{job_id}.json"

    @staticmethod
    def _write(job_id: str, payload: dict[str, Any]) -> None:
        # Only writers create the job root: a GET for an unknown job must not
        # leave directories behind.
        path = OntologyJobService._job_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _age_seconds(job: dict[str, Any]) -> Optional[float]:
        try:
            updated = time.strptime(job["updated_at"], "%Y-%m-%dT%H:%M:%S")
        except (KeyError, ValueError):
            return None  # malformed/missing timestamp
        return time.time() - time.mktime(updated)

    @staticmethod
    def _is_live(job: dict[str, Any], max_age_seconds: float = STALE_JOB_SECONDS) -> bool:
        if job.get("status") not in ("queued", "running"):
            return False
        age = OntologyJobService._age_seconds(job)
        if age is None:
            return True  # malformed timestamp — don't punish the job for it
        return age <= max_age_seconds

    @staticmethod
    def reap_orphaned_jobs() -> int:
        """Mark queued/running jobs untouched for STARTUP_REAP_GRACE_SECONDS as
        failed. Call once when the ontology API is wired up (build_ontology_
        router() does this) — a freshly booted process's own _active_job_id is
        always None, so any job that's still queued/running *and* hasn't been
        updated recently is unambiguously orphaned (the thread that was
        advancing it died with whatever process started it). See the
        STARTUP_REAP_GRACE_SECONDS comment for why this uses a short window
        instead of reaping everything queued/running unconditionally — that
        would risk killing a job a different, still-alive process is actively
        driving right now (JOB_ROOT is shared by every backend process
        serving the same hermes profile).
        """
        if not OntologyJobService.JOB_ROOT.exists():
            return 0
        reaped = 0
        for path in OntologyJobService.JOB_ROOT.glob("job-*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") not in ("queued", "running"):
                continue
            age = OntologyJobService._age_seconds(job)
            if age is not None and age <= STARTUP_REAP_GRACE_SECONDS:
                continue  # recently updated — some other live process owns it
            job["status"] = "failed"
            job["stage"] = "failed"
            job["error"] = "orphaned: the backend process advancing this scan was restarted"
            job["batch_label"] = "扫描失败（后端重启）"
            job["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            try:
                path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
                reaped += 1
            except OSError:
                logger.exception("failed to reap orphaned job file %s", path)
        if reaped:
            logger.warning("reaped %d orphaned ontology scan job(s) on startup", reaped)
        return reaped

    @staticmethod
    def create_scan_job() -> dict[str, Any]:
        with OntologyJobService._lock:
            active_id = OntologyJobService._active_job_id
            if active_id:
                try:
                    existing = OntologyJobService.get_job(active_id)
                except OntologyError:
                    existing = None
                if existing and OntologyJobService._is_live(existing):
                    # A scan is already in flight (e.g. a stale tab re-clicked
                    # "Run Scan") — hand back the same job instead of spawning
                    # a second AI judgment pass concurrently.
                    return existing
                OntologyJobService._active_job_id = None

            job_id = f"job-{uuid.uuid4().hex[:10]}"
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            payload = {
                "job_id": job_id,
                "status": "queued",
                "stage": "queued",
                "progress": 0.0,
                "scan_id": None,
                "output_dir": None,
                "score": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            OntologyJobService._write(job_id, payload)
            OntologyJobService._active_job_id = job_id
            t = Thread(target=OntologyJobService._run_scan_job, args=(job_id,), daemon=True)
            t.start()
            return payload

    @staticmethod
    def _clear_active(job_id: str) -> None:
        with OntologyJobService._lock:
            if OntologyJobService._active_job_id == job_id:
                OntologyJobService._active_job_id = None

    @staticmethod
    def get_active_job() -> Optional[dict[str, Any]]:
        """Return the currently in-flight job, or None if nothing is running.

        Lets a freshly (re)mounted page ask "is a scan already running" without
        needing to already hold a job_id — the source of truth is this
        server-side pointer (backed by JOB_ROOT on disk), not anything the
        frontend has to remember across navigations.
        """
        with OntologyJobService._lock:
            active_id = OntologyJobService._active_job_id
        if active_id:
            try:
                job = OntologyJobService.get_job(active_id)
            except OntologyError:
                job = None
            if job and OntologyJobService._is_live(job):
                return job
            OntologyJobService._clear_active(active_id)
        # Fallback for the (rare) case the in-memory pointer was lost — e.g.
        # this process restarted right after spawning the thread but before
        # this method's caller ever asked. Scan JOB_ROOT for the most recent
        # job that still looks alive; if the thread that was advancing it also
        # died in that restart, STALE_JOB_SECONDS will age it out on its own.
        if not OntologyJobService.JOB_ROOT.exists():
            return None
        candidates = []
        for path in OntologyJobService.JOB_ROOT.glob("job-*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if OntologyJobService._is_live(job):
                candidates.append(job)
        if not candidates:
            return None
        candidates.sort(key=lambda j: j.get("updated_at", ""), reverse=True)
        return candidates[0]

    @staticmethod
    def _update(job_id: str, **kwargs: Any) -> None:
        p = OntologyJobService.get_job(job_id)
        p.update(kwargs)
        p["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        OntologyJobService._write(job_id, p)

    @staticmethod
    def _run_scan_job(job_id: str) -> None:
        try:
            OntologyJobService._run_scan_job_body(job_id)
        finally:
            # Whatever happened — success, known failure, or a crash the
            # except clauses below don't even catch — this job is no longer
            # in flight, so stop advertising it via get_active_job().
            OntologyJobService._clear_active(job_id)

    @staticmethod
    def _run_scan_job_body(job_id: str) -> None:
        try:
            OntologyJobService._update(
                job_id, status="running", stage="collecting_evidence", progress=0.05
            )
            collect_result = OntologyAIService.collect()
            bundle_path = str(Path(collect_result["output_dir"]) / "evidence-bundle.json")
            OntologyJobService._update(
                job_id,
                stage="judging_with_ai",
                progress=0.4,
                current_batch=0,
                total_batches=None,
                batch_label="AI 判定准备中",
            )

            def on_batch(batch_index: int, total_batches: int) -> None:
                span = 0.34
                progress = 0.4 + (batch_index - 1) / max(total_batches, 1) * span
                OntologyJobService._update(
                    job_id,
                    stage="judging_with_ai",
                    progress=round(progress, 3),
                    current_batch=batch_index,
                    total_batches=total_batches,
                    batch_label=f"AI 判定第 {batch_index}/{total_batches} 批",
                )

            judgments_path = OntologyAIService.judge(bundle_path, progress_cb=on_batch)
            OntologyJobService._update(
                job_id,
                stage="scoring_and_gap_analysis",
                progress=0.78,
                batch_label="差异评分与聚合中",
            )
            score_result = OntologyAIService.score(judgments_path, promote_latest=True)
            score_outdir = Path(score_result["output_dir"])
            try:
                score_outdir.joinpath("judgments.runtime.json").write_text(
                    Path(judgments_path).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            except Exception:
                logger.exception("could not persist runtime judgments to scan dir")
            OntologyJobService._update(
                job_id,
                status="completed",
                stage="completed",
                progress=1.0,
                scan_id=score_result["scan_id"],
                output_dir=score_result["output_dir"],
                score=score_result["completeness_score"],
                error=None,
                batch_label="扫描完成",
            )
        except (OntologyAIError, OntologyError) as exc:
            logger.warning("ontology scan job %s failed: %s", job_id, exc)
            OntologyJobService._update(
                job_id, status="failed", stage="failed", progress=1.0, error=str(exc), batch_label="扫描失败"
            )
        except Exception as exc:
            logger.exception("ontology scan job %s crashed", job_id)
            OntologyJobService._update(
                job_id,
                status="failed",
                stage="failed",
                progress=1.0,
                error=f"internal error: {exc}",
                batch_label="扫描失败",
            )

    @staticmethod
    def get_job(job_id: str) -> dict[str, Any]:
        p = OntologyJobService._job_path(job_id)
        if not p.exists():
            raise OntologyError(f"Ontology scan job not found: {job_id}")
        return json.loads(p.read_text(encoding="utf-8"))
