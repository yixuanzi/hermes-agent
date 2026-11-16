"""AISOC ontology onboarding routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from aisoc.backend.services.ontology_schemas import (
    OntologyFilePayload,
    OntologyOverviewResponse,
    OntologyRoadmapResponse,
    OntologyScanJobResponse,
    OntologyScanResponse,
)
from aisoc.backend.services.ontology_job_service import OntologyJobService
from aisoc.backend.services.ontology_service import (
    OntologyBusyError,
    OntologyError,
    OntologyService,
    OntologyTimeoutError,
)


logger = logging.getLogger(__name__)


def _run_mutation(label: str, fn):
    """Invoke a compile/scan-style mutation and translate service exceptions
    into HTTP responses without leaking internals."""
    try:
        return fn()
    except OntologyBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except OntologyTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except OntologyError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    except Exception:
        logger.exception("ontology %s unexpected error", label)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ontology {label} failed",
        )


def build_ontology_router() -> APIRouter:
    # Any scan job still marked queued/running from a *previous* incarnation
    # of this process (e.g. it was killed/restarted mid-scan) is orphaned —
    # its daemon thread died with it, so it will never progress again. Reap
    # those now so GET /scan/active doesn't keep reporting a dead job as
    # "running" until STALE_JOB_SECONDS (20 min) finally ages it out.
    OntologyJobService.reap_orphaned_jobs()

    router = APIRouter(prefix="/api/ontology", tags=["ontology"])

    @router.post("/compile", response_model=OntologyScanResponse)
    async def compile_standard_graph() -> dict:
        result = _run_mutation("compile", OntologyService.compile_standard_graph)
        return {
            "scan_id": "standard-compile",
            "output_dir": result["output"],
            "score": result["weight_total"],
        }

    @router.post("/scan", response_model=OntologyScanJobResponse)
    async def run_ontology_scan() -> dict:
        # 异步 job：立即返回 job_id，实际扫描在后台线程完成。客户端轮询 /scan/{job_id}。
        # 若已有扫描在跑，create_scan_job 会直接返回同一个 job（不会重复起 AI 判定）。
        return _run_mutation("scan", OntologyJobService.create_scan_job)

    # NOTE: must be registered before /scan/{job_id} — otherwise a GET to
    # /scan/active would match the {job_id} route with job_id="active" first.
    @router.get("/scan/active", response_model=OntologyScanJobResponse)
    async def get_active_ontology_scan() -> dict:
        # 页面刚挂载时用这个查"现在有没有扫描在跑"，不需要提前记住 job_id ——
        # 无论是切换 tab 回来、刷新页面、还是换个浏览器，只要后端进程没重启，
        # 都能接上正在跑的那个 job。
        job = OntologyJobService.get_active_job()
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No ontology scan is currently running")
        return job

    @router.get("/scan/{job_id}", response_model=OntologyScanJobResponse)
    async def get_ontology_scan_job(job_id: str) -> dict:
        try:
            return OntologyJobService.get_job(job_id)
        except OntologyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    @router.get("/overview", response_model=OntologyOverviewResponse)
    async def ontology_overview() -> OntologyOverviewResponse:
        try:
            return OntologyOverviewResponse(**OntologyService.get_overview())
        except OntologyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            )

    @router.get("/artifacts/{name}", response_model=OntologyFilePayload)
    async def ontology_artifact(name: str) -> OntologyFilePayload:
        try:
            return OntologyFilePayload(data=OntologyService.get_artifact(name))
        except OntologyError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            )

    @router.get("/roadmap", response_model=OntologyRoadmapResponse)
    async def ontology_roadmap() -> OntologyRoadmapResponse:
        try:
            return OntologyRoadmapResponse(**OntologyService.build_roadmap())
        except OntologyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            )

    return router
