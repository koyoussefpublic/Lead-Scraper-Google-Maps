"""REST API endpoints for jobs, logs, downloads, and metadata."""

import io
import mimetypes
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from app.auth import get_current_user
from app.jobs import job_manager
from app.models import JobCreate, JobDetail, JobStatus
from app.queue import enqueue_job
from config.categories import CATEGORIES
from config.metros import METROS

router = APIRouter(prefix="/api", tags=["API"])

ALLOWED_DOWNLOAD_FILES = {
    "leads_raw.csv": "text/csv",
    "leads_qualified.csv": "text/csv",
    "leads.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "scraper.log": "text/plain",
}


@router.post("/jobs", response_model=JobStatus, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreate,
    current_user: str = Depends(get_current_user),
):
    """Start a new background scraping job."""
    params = payload.model_dump()
    job = job_manager.create_job(params)
    await enqueue_job(job.job_id)
    return job


@router.get("/jobs", response_model=list[JobStatus])
async def list_jobs(
    current_user: str = Depends(get_current_user),
):
    """List all scraping jobs (newest first)."""
    return job_manager.list_jobs()


@router.get("/jobs/export/zip")
async def export_all_jobs_zip(
    current_user: str = Depends(get_current_user),
):
    """Export all generated output files across all jobs into a single ZIP archive."""
    jobs = job_manager.list_jobs()
    zip_buffer = io.BytesIO()
    added_count = 0

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for job in jobs:
            try:
                job_dir = job_manager.get_job_dir(job.job_id).resolve()
            except ValueError:
                continue

            for filename in ALLOWED_DOWNLOAD_FILES:
                target_file = (job_dir / filename).resolve()
                if target_file.is_relative_to(job_dir) and target_file.is_file():
                    zf.write(target_file, arcname=f"{job.job_id}/{filename}")
                    added_count += 1

    if added_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No exportable files found across any jobs.",
        )

    zip_buffer.seek(0)
    zip_filename = f"all_jobs_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


@router.post("/jobs/{job_id}/duplicate", response_model=JobStatus, status_code=status.HTTP_201_CREATED)
async def duplicate_job(
    job_id: str,
    current_user: str = Depends(get_current_user),
):
    """Duplicate an existing job with the same parameters and enqueue it."""
    existing = job_manager.get_job(job_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found."
        )

    validated = JobCreate(**existing.params)
    new_job = job_manager.create_job(validated.model_dump())
    await enqueue_job(new_job.job_id)
    return new_job


@router.get("/jobs/{job_id}", response_model=JobDetail)
async def get_job_detail(
    job_id: str,
    current_user: str = Depends(get_current_user),
):
    """Get status, metrics, and recent logs for a specific job."""
    job = job_manager.get_job(job_id, include_logs=True)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found."
        )
    return job


@router.get("/jobs/{job_id}/logs")
async def get_job_logs(
    job_id: str,
    tail: int = Query(default=200, ge=1, le=2000),
    current_user: str = Depends(get_current_user),
):
    """Return raw log lines for a specific job."""
    if not job_manager.get_job(job_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found."
        )
    logs = job_manager.get_job_logs(job_id, tail=tail)
    return PlainTextResponse("\n".join(logs))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    current_user: str = Depends(get_current_user),
):
    """Cancel a running or queued job."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found."
        )
    if job.status in ("completed", "failed", "cancelled"):
        return {"status": "ignored", "message": f"Job is already in '{job.status}' state."}

    success = job_manager.cancel_job(job_id)
    return {"status": "cancelled" if success else "failed", "job_id": job_id}


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    current_user: str = Depends(get_current_user),
):
    """Delete a job and remove its files."""
    if not job_manager.get_job(job_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found."
        )
    success = job_manager.delete_job(job_id)
    return {"status": "deleted" if success else "failed", "job_id": job_id}


@router.get("/jobs/{job_id}/download/{filename}")
async def download_job_file(
    job_id: str,
    filename: str,
    current_user: str = Depends(get_current_user),
):
    """Securely download generated output files (leads_raw.csv, leads_qualified.csv, leads.xlsx, scraper.log)."""
    # 1. Filename validation against strict whitelist
    if filename not in ALLOWED_DOWNLOAD_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file request. Allowed files: {', '.join(ALLOWED_DOWNLOAD_FILES.keys())}",
        )

    # 2. Path resolution and traversal prevention
    try:
        job_dir = job_manager.get_job_dir(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid job ID."
        )

    target_file = (job_dir / filename).resolve()
    if not target_file.is_relative_to(job_dir.resolve()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied."
        )

    if not target_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{filename}' has not been generated for this job yet.",
        )

    media_type = ALLOWED_DOWNLOAD_FILES.get(filename, "application/octet-stream")
    download_name = f"{job_id}_{filename}"
    return FileResponse(
        path=target_file,
        media_type=media_type,
        filename=download_name,
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


@router.get("/categories")
async def list_categories():
    """Return built-in category suggestions."""
    return {"categories": CATEGORIES}


@router.get("/metros")
async def list_metros():
    """Return built-in metro presets."""
    return {"metros": METROS}


@router.get("/health")
async def api_health():
    """Healthcheck endpoint for monitoring and Railway probes."""
    return {"status": "ok", "app": "google-maps-lead-scraper"}
