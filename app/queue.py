"""Background job queue and programmatic scraper executor."""

import asyncio
import logging
from typing import Any

from app import config
from app.jobs import job_manager
from app.reporter import WebReporter
from config.enums import Depth, LeadProfile
from config.paths import OutputPaths
from config.runconfig import RunConfig
from runner import ScraperRun

logger = logging.getLogger(__name__)

# In-memory async queue for standalone/local/single-service deployment
_async_queue: asyncio.Queue = asyncio.Queue()
_worker_running: bool = False
_worker_task: asyncio.Task | None = None
_concurrency_semaphore: asyncio.Semaphore | None = None


def get_redis_client():
    """Return a connected Redis client if REDIS_URL is configured."""
    if not config.REDIS_URL:
        return None
    try:
        import redis.asyncio as aioredis
        return aioredis.from_url(config.REDIS_URL, decode_responses=True)
    except Exception as exc:
        logger.error(f"Could not connect to Redis at {config.REDIS_URL}: {exc}")
        return None


async def run_scraper_job(job_id: str) -> None:
    """Execute the scraping workflow programmatically for a specific job."""
    job_status = job_manager.get_job(job_id)
    if not job_status:
        logger.error(f"Job {job_id} not found for execution.")
        return

    job_dir = job_manager.get_job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    output_paths = OutputPaths(root=job_dir)
    params = job_status.params

    reporter = WebReporter(
        job_id=job_id,
        log_file=output_paths.log_file,
        on_update=lambda snapshot: job_manager.update_progress(job_id, snapshot),
    )

    try:
        depth = Depth(params.get("depth", "city"))
    except ValueError:
        depth = Depth.CITY

    try:
        profile = LeadProfile(params.get("profile", "no_website"))
    except ValueError:
        profile = LeadProfile.NO_WEBSITE

    cfg = RunConfig(
        target_input=params.get("city", ""),
        categories=params.get("categories", []),
        depth=depth,
        profile=profile,
        max_results_per_unit=params.get("max_results"),
        grid_rows=int(params.get("grid_rows", 3)),
        grid_cols=int(params.get("grid_cols", 3)),
        grid_span_km=float(params.get("grid_span_km", 18.0)),
        require_phone=bool(params.get("require_phone", True)),
        fetch_website_email=bool(params.get("crawl_websites", True)),
        concurrency=int(params.get("concurrency", 1)),
        headless=config.HEADLESS,
        tui=False,
        output=output_paths,
    )

    job_manager.mark_started(job_id)
    reporter.on_phase(f"Initializing scraper for '{cfg.target_input}'")

    current_task = asyncio.current_task()
    if current_task:
        job_manager.register_task(job_id, current_task)

    try:
        runner = ScraperRun(cfg, reporter=reporter)
        stats = await runner.execute()

        # Update final stats snapshot
        job_manager.update_progress(
            job_id,
            {
                "current_phase": "Completed",
                "total_units": stats.total_units,
                "units_done": stats.units_done,
                "units_failed": stats.units_failed,
                "units_skipped": stats.units_skipped,
                "found": stats.found,
                "unique": stats.unique,
                "duplicates": stats.duplicates,
                "qualified": stats.qualified,
                "blocks": stats.blocks,
                "elapsed_seconds": stats.elapsed,
            },
        )

        if stats.aborted_reason and "Interrupted" not in stats.aborted_reason:
            job_manager.mark_failed(job_id, stats.aborted_reason)
        else:
            job_manager.mark_completed(job_id)

    except asyncio.CancelledError:
        logger.warning(f"Job {job_id} was cancelled.")
        job_manager.mark_cancelled(job_id)
        reporter.on_phase("Cancelled by user")
        reporter.on_log("Job was cancelled.", level="warning")
    except Exception as exc:
        logger.exception(f"Unhandled error in job {job_id}: {exc}")
        job_manager.mark_failed(job_id, str(exc))
        reporter.on_phase("Failed")
        reporter.on_log(f"Job failed with error: {exc}", level="error")
    finally:
        job_manager.unregister_task(job_id)


async def enqueue_job(job_id: str) -> None:
    """Add a job to the queue (either Redis or internal queue)."""
    redis_client = get_redis_client()
    if redis_client:
        try:
            await redis_client.rpush(config.REDIS_QUEUE_KEY, job_id)
            await redis_client.aclose()
            logger.info(f"Enqueued job {job_id} to Redis queue.")
            return
        except Exception as exc:
            logger.error(f"Failed to enqueue to Redis, falling back to local queue: {exc}")

    await _async_queue.put(job_id)
    logger.info(f"Enqueued job {job_id} to local async queue.")


async def _worker_loop() -> None:
    """Background worker processing jobs one by one up to concurrency limit."""
    global _worker_running, _concurrency_semaphore
    if _concurrency_semaphore is None:
        _concurrency_semaphore = asyncio.Semaphore(config.CONCURRENCY_LIMIT)

    logger.info(f"Background scraper worker started (concurrency={config.CONCURRENCY_LIMIT}).")
    _worker_running = True

    while _worker_running:
        job_id: str | None = None
        redis_client = get_redis_client()

        if redis_client:
            try:
                # Block for up to 2 seconds waiting for a job
                result = await redis_client.blpop(config.REDIS_QUEUE_KEY, timeout=2)
                if result:
                    _, job_id = result
                await redis_client.aclose()
            except Exception as exc:
                logger.debug(f"Redis poll error: {exc}")
                await asyncio.sleep(1)

        if not job_id:
            try:
                job_id = await asyncio.wait_for(_async_queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

        if job_id:
            async def _run_guarded(jid: str):
                async with _concurrency_semaphore:
                    await run_scraper_job(jid)
                if not config.REDIS_URL:
                    _async_queue.task_done()

            asyncio.create_task(_run_guarded(job_id))


def start_worker() -> None:
    """Start the background worker task if not already running."""
    global _worker_task, _worker_running
    if _worker_task is None or _worker_task.done():
        _worker_running = True
        _worker_task = asyncio.create_task(_worker_loop())


async def stop_worker() -> None:
    """Stop the background worker."""
    global _worker_running, _worker_task
    _worker_running = False
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
