"""Cron routes for AISOC backend."""

from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException

from aisoc.backend.models import CronJobCreate, CronJobRawUpdate, CronJobUpdate
from aisoc.backend.services import cron_service


def build_cron_router() -> APIRouter:
    router = APIRouter(prefix="/api/cron", tags=["cron"])

    def _default_profile(profile: str | None) -> str:
        raw = (profile or "").strip()
        if raw:
            return raw
        return cron_service.get_runtime_profile_name()

    def _job_profile(profile: str | None) -> str | None:
        resolved = _default_profile(profile)
        if resolved.lower() == "all":
            return None
        return resolved

    @router.get("/jobs")
    async def list_jobs(
        profile: str | None = None,
        page: int = 1,
        page_size: int = 12,
    ):
        safe_page = max(1, page)
        safe_page_size = max(1, page_size)
        jobs = cron_service.list_jobs(profile=_default_profile(profile))
        total = len(jobs)
        total_pages = max(1, math.ceil(total / safe_page_size))
        current_page = min(safe_page, total_pages)
        start = (current_page - 1) * safe_page_size
        end = start + safe_page_size
        items = jobs[start:end]
        return {
            "items": items,
            "page": current_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
            "has_prev": current_page > 1,
            "has_next": current_page < total_pages,
        }

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str, profile: str | None = None):
        job = cron_service.get_job(job_id, profile=_job_profile(profile))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @router.get("/jobs/{job_id}/history")
    async def get_job_history(job_id: str, profile: str | None = None):
        payload = cron_service.get_job_history(job_id, profile=_job_profile(profile))
        if payload is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return payload

    @router.post("/jobs")
    async def create_job(body: CronJobCreate, profile: str | None = None):
        selected_profile = _default_profile(profile)
        return cron_service.create_job(
            selected_profile,
            name=body.name,
            prompt=body.prompt,
            schedule=body.schedule,
            deliver=body.deliver,
            skills=body.skills,
            skill=body.skill,
            enabled_toolsets=body.enabled_toolsets,
            model=body.model,
            provider=body.provider,
            base_url=body.base_url,
            script=body.script,
            workdir=body.workdir,
            no_agent=body.no_agent,
        )

    @router.put("/jobs/{job_id}")
    async def update_job(job_id: str, body: CronJobUpdate, profile: str | None = None):
        job = cron_service.update_job(job_id, body.updates, profile=_job_profile(profile))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @router.put("/jobs/{job_id}/raw")
    async def update_job_raw(job_id: str, body: CronJobRawUpdate, profile: str | None = None):
        job = cron_service.update_job_raw(job_id, body.job, profile=_job_profile(profile))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @router.post("/jobs/{job_id}/pause")
    async def pause_job(job_id: str, profile: str | None = None):
        job = cron_service.pause_job(job_id, profile=_job_profile(profile))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @router.post("/jobs/{job_id}/resume")
    async def resume_job(job_id: str, profile: str | None = None):
        job = cron_service.resume_job(job_id, profile=_job_profile(profile))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @router.post("/jobs/{job_id}/trigger")
    async def trigger_job(job_id: str, profile: str | None = None):
        job = cron_service.trigger_job(job_id, profile=_job_profile(profile))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @router.delete("/jobs/{job_id}")
    async def delete_job(job_id: str, profile: str | None = None):
        if not cron_service.remove_job(job_id, profile=_job_profile(profile)):
            raise HTTPException(status_code=404, detail="Job not found")
        return {"ok": True}

    return router
