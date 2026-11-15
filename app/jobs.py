"""Job state tracking, persistence, and output directory management."""

import asyncio
import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app import config
from app.models import JobDetail, JobFiles, JobStats, JobStatus

logger = logging.getLogger(__name__)


class JobManager:
    """Manages job records, persistent metadata, and execution tasks."""

    def __init__(self, root_dir: Path | None = None):
        self.root_dir = root_dir or config.OUTPUT_DIR
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._load_existing_jobs()

    def get_job_dir(self, job_id: str) -> Path:
        """Return the isolated output directory for a specific job."""
        # Sanitize job_id to prevent any directory traversal
        safe_id = "".join(c for c in job_id if c.isalnum() or c in "-_")
        path = (self.root_dir / safe_id).resolve()
        # Verify it stays strictly within root_dir
        if not path.is_relative_to(self.root_dir.resolve()):
            raise ValueError("Invalid job directory path")
        return path

    def _get_meta_path(self, job_id: str) -> Path:
        return self.get_job_dir(job_id) / "meta.json"

    def _check_files(self, job_id: str) -> JobFiles:
        job_dir = self.get_job_dir(job_id)
        return JobFiles(
            raw_csv=(job_dir / "leads_raw.csv").is_file(),
            qualified_csv=(job_dir / "leads_qualified.csv").is_file(),
            xlsx=(job_dir / "leads.xlsx").is_file(),
            log=(job_dir / "scraper.log").is_file(),
        )

    def _load_existing_jobs(self):
        """Discover existing job directories in the output root (e.g. from volume)."""
        if not self.root_dir.exists():
            return
        for entry in self.root_dir.iterdir():
            if entry.is_dir():
                meta_file = entry / "meta.json"
                if meta_file.is_file():
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            job_id = data.get("job_id")
                            if job_id:
                                # If server restarted while job was marked running, mark it failed/interrupted
                                if data.get("status") in ("running", "queued"):
                                    data["status"] = "failed"
                                    data["error"] = "Process terminated unexpectedly (server restart)"
                                self._jobs[job_id] = data
                    except Exception as exc:
                        logger.warning(f"Could not load job meta from {meta_file}: {exc}")

    def _save_job_meta(self, job_id: str):
        data = self._jobs.get(job_id)
        if not data:
            return
        try:
            job_dir = self.get_job_dir(job_id)
            job_dir.mkdir(parents=True, exist_ok=True)
            meta_path = self._get_meta_path(job_id)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.error(f"Failed to save job metadata for {job_id}: {exc}")

    def create_job(self, params: dict[str, Any]) -> JobStatus:
        """Create a new queued job and allocate its output directory."""
        job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        now_str = datetime.now().isoformat()

        job_dir = self.get_job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)

        record: dict[str, Any] = {
            "job_id": job_id,
            "status": "queued",
            "params": params,
            "stats": JobStats().model_dump(),
            "created_at": now_str,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "current_phase": "Queued",
            "recent_logs": [f"[{datetime.now().strftime('%H:%M:%S')}] Job created and queued."],
        }

        self._jobs[job_id] = record
        self._save_job_meta(job_id)
        return self.get_job(job_id)

    def mark_started(self, job_id: str):
        record = self._jobs.get(job_id)
        if record:
            record["status"] = "running"
            record["started_at"] = datetime.now().isoformat()
            record["current_phase"] = "Starting"
            self._save_job_meta(job_id)

    def mark_completed(self, job_id: str):
        record = self._jobs.get(job_id)
        if record:
            record["status"] = "completed"
            record["completed_at"] = datetime.now().isoformat()
            record["current_phase"] = "Completed"
            self._save_job_meta(job_id)

    def mark_failed(self, job_id: str, error_message: str):
        record = self._jobs.get(job_id)
        if record:
            record["status"] = "failed"
            record["completed_at"] = datetime.now().isoformat()
            record["error"] = error_message
            record["current_phase"] = "Failed"
            self._save_job_meta(job_id)

    def mark_cancelled(self, job_id: str):
        record = self._jobs.get(job_id)
        if record:
            record["status"] = "cancelled"
            record["completed_at"] = datetime.now().isoformat()
            record["current_phase"] = "Cancelled"
            self._save_job_meta(job_id)

    def update_progress(self, job_id: str, snapshot: dict[str, Any]):
        record = self._jobs.get(job_id)
        if not record:
            return

        record["current_phase"] = snapshot.get("current_phase", record.get("current_phase"))
        if "recent_logs" in snapshot:
            record["recent_logs"] = snapshot["recent_logs"]

        stats = record.setdefault("stats", {})
        for k in (
            "total_units", "units_done", "units_failed", "units_skipped",
            "found", "unique", "duplicates", "qualified", "blocks",
            "elapsed_seconds", "eta_seconds",
        ):
            if k in snapshot:
                stats[k] = snapshot[k]

        # Periodically save meta to disk
        self._save_job_meta(job_id)

    def register_task(self, job_id: str, task: asyncio.Task):
        self._running_tasks[job_id] = task

    def unregister_task(self, job_id: str):
        self._running_tasks.pop(job_id, None)

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running or queued job."""
        record = self._jobs.get(job_id)
        if not record:
            return False

        if record["status"] in ("completed", "failed", "cancelled"):
            return False

        task = self._running_tasks.get(job_id)
        if task and not task.done():
            task.cancel()

        self.mark_cancelled(job_id)
        return True

    def delete_job(self, job_id: str) -> bool:
        """Delete a job record and its output folder."""
        record = self._jobs.pop(job_id, None)
        if not record:
            return False

        task = self._running_tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        self._running_tasks.pop(job_id, None)

        try:
            job_dir = self.get_job_dir(job_id)
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
            return True
        except Exception as exc:
            logger.error(f"Failed to delete directory for job {job_id}: {exc}")
            return False

    def list_jobs(self) -> list[JobStatus]:
        """List all jobs sorted from newest to oldest."""
        result = []
        for job_id, data in self._jobs.items():
            files = self._check_files(job_id)
            stats = JobStats(**data.get("stats", {}))
            status_obj = JobStatus(
                job_id=job_id,
                status=data.get("status", "unknown"),
                params=data.get("params", {}),
                stats=stats,
                created_at=data.get("created_at", ""),
                started_at=data.get("started_at"),
                completed_at=data.get("completed_at"),
                error=data.get("error"),
                current_phase=data.get("current_phase"),
                files=files,
            )
            result.append(status_obj)

        result.sort(key=lambda j: j.created_at, reverse=True)
        return result

    def get_job(self, job_id: str, include_logs: bool = False) -> JobDetail | JobStatus | None:
        """Get status or full details for a job."""
        data = self._jobs.get(job_id)
        if not data:
            return None

        files = self._check_files(job_id)
        stats = JobStats(**data.get("stats", {}))
        base_args = dict(
            job_id=job_id,
            status=data.get("status", "unknown"),
            params=data.get("params", {}),
            stats=stats,
            created_at=data.get("created_at", ""),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            current_phase=data.get("current_phase"),
            files=files,
        )

        if include_logs:
            # Read logs from in-memory or log file
            logs = data.get("recent_logs", [])
            log_file = self.get_job_dir(job_id) / "scraper.log"
            if not logs and log_file.is_file():
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        logs = [line.strip() for line in f.readlines()[-300:]]
                except Exception:
                    pass
            return JobDetail(**base_args, recent_logs=logs)

        return JobStatus(**base_args)

    def get_job_logs(self, job_id: str, tail: int = 300) -> list[str]:
        record = self._jobs.get(job_id)
        if not record:
            return []
        log_file = self.get_job_dir(job_id) / "scraper.log"
        if log_file.is_file():
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    lines = [line.rstrip() for line in f.readlines()]
                    return lines[-tail:] if tail > 0 else lines
            except Exception:
                pass
        return record.get("recent_logs", [])


# Global singleton instance
job_manager = JobManager()
