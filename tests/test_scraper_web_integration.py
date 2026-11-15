"""Tests for programmatic scraper execution, WebReporter, and job lifecycle."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.jobs import job_manager
from app.queue import run_scraper_job
from app.reporter import WebReporter
from data.models import Business
from reporting import RunStats


def test_web_reporter_events_and_file_logging(tmp_path):
    """Verify WebReporter records logs, fires on_update callbacks, and writes scraper.log."""
    log_file = tmp_path / "scraper.log"
    updates = []

    reporter = WebReporter(
        job_id="test_job_1",
        log_file=log_file,
        on_update=lambda snapshot: updates.append(snapshot),
    )

    stats = RunStats(total_units=2)
    reporter.start(stats)
    reporter.on_phase("Resolving target city...")

    mock_biz = Business(name="Apex Electric", phone="+15551234567")
    reporter.on_business(mock_biz, unique=True, qualified=True)

    reporter.on_log("Found 1 new result")
    reporter.stop()

    assert len(updates) > 0
    latest = reporter.get_snapshot()
    assert latest["current_phase"] == "Completed"
    assert any("Apex Electric" in line for line in latest["recent_logs"])

    # Verify file on disk
    assert log_file.is_file()
    content = log_file.read_text(encoding="utf-8")
    assert "Apex Electric" in content
    assert "Resolving target city..." in content


@pytest.mark.asyncio
async def test_simulated_scraper_job_execution():
    """Simulated test: verify programmatic execution flow and state transitions."""
    job = job_manager.create_job(
        {
            "city": "Austin TX",
            "categories": ["plumbers"],
            "depth": "city",
            "profile": "no_website",
        }
    )
    job_id = job.job_id

    # Mock ScraperRun to simulate a browser scraping run without hitting live Google Maps
    mock_stats = RunStats(
        total_units=1,
        units_done=1,
        units_failed=0,
        found=10,
        unique=8,
        qualified=5,
    )

    async def mock_execute(self):
        # Simulate business export
        self.stats.total_units = 1
        self.stats.units_done = 1
        self.stats.found = 10
        self.stats.unique = 8
        self.stats.qualified = 5
        self.exporter.add_raw(Business(name="Austin Plumbing Co", phone="+15125550100"))
        self.exporter.add_qualified(Business(name="Austin Plumbing Co", phone="+15125550100"))
        self.exporter.flush()
        self.exporter.export_xlsx()
        return self.stats

    with patch("app.queue.ScraperRun.execute", mock_execute):
        await run_scraper_job(job_id)

    # Verify status is completed
    finished_job = job_manager.get_job(job_id, include_logs=True)
    assert finished_job.status == "completed"
    assert finished_job.stats.found == 10
    assert finished_job.stats.qualified == 5

    # Verify output files exist
    assert finished_job.files.raw_csv is True
    assert finished_job.files.qualified_csv is True
    assert finished_job.files.xlsx is True

    job_manager.delete_job(job_id)


@pytest.mark.asyncio
async def test_scraper_job_error_handling():
    """Verify unhandled exceptions in the scraper mark the job as failed."""
    job = job_manager.create_job({"city": "Error City", "categories": ["plumbers"]})
    job_id = job.job_id

    async def mock_fail_execute(self):
        raise RuntimeError("Simulated browser crash or network failure")

    with patch("app.queue.ScraperRun.execute", mock_fail_execute):
        await run_scraper_job(job_id)

    failed_job = job_manager.get_job(job_id)
    assert failed_job.status == "failed"
    assert "Simulated browser crash" in failed_job.error

    job_manager.delete_job(job_id)
