"""Dedicated worker process entry point for Railway worker service."""

import asyncio
import logging
import signal
import sys
from app import config
from app.queue import _worker_loop, stop_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("gmaps_scraper.worker")


async def main():
    logger.info("Starting standalone background scraper worker...")
    logger.info(f"Storage directory: {config.OUTPUT_DIR}")
    logger.info(f"Concurrency limit: {config.CONCURRENCY_LIMIT}")
    logger.info(f"Redis Queue: {'Enabled (' + config.REDIS_URL + ')' if config.REDIS_URL else 'Disabled (Local only)'}")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal():
        logger.info("Received termination signal, shutting down worker...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows does not implement loop.add_signal_handler
            pass

    worker_task = asyncio.create_task(_worker_loop())

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await stop_worker()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        logger.info("Worker process exited cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
