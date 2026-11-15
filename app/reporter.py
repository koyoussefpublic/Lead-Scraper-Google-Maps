"""Web-oriented Reporter implementing the scraper's reporting interface."""

import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

from geo.models import SearchUnit
from reporting import Reporter, RunStats

logger = logging.getLogger(__name__)


class WebReporter(Reporter):
    """Bridges ScraperRun events to web dashboard state and job logs."""

    def __init__(
        self,
        job_id: str,
        log_file: Path,
        on_update: Callable[[dict], None] | None = None,
        max_logs: int = 300,
    ):
        self.job_id = job_id
        self.log_file = log_file
        self.on_update = on_update
        self.max_logs = max_logs

        self._lock = threading.Lock()
        self.logs: deque[str] = deque(maxlen=max_logs)
        self.current_phase: str = "Initializing"
        self.current_unit_label: str = ""
        self.unit_done: int = 0
        self.unit_total: int = 0
        self.stats: RunStats = RunStats()

        log_file.parent.mkdir(parents=True, exist_ok=True)

    def _append_log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] [{level.upper()}] {message}"
        with self._lock:
            self.logs.append(entry)

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception as exc:
            logger.debug(f"Could not write log file: {exc}")

        self._notify()

    def _notify(self):
        if not self.on_update:
            return
        try:
            snapshot = self.get_snapshot()
            self.on_update(snapshot)
        except Exception as exc:
            logger.debug(f"Error calling on_update in WebReporter: {exc}")

    def get_snapshot(self) -> dict:
        with self._lock:
            return {
                "current_phase": self.current_phase,
                "current_unit": self.current_unit_label,
                "unit_progress": {"done": self.unit_done, "total": self.unit_total},
                "total_units": self.stats.total_units,
                "units_done": self.stats.units_done,
                "units_failed": self.stats.units_failed,
                "units_skipped": self.stats.units_skipped,
                "found": self.stats.found,
                "unique": self.stats.unique,
                "duplicates": self.stats.duplicates,
                "qualified": self.stats.qualified,
                "blocks": self.stats.blocks,
                "elapsed_seconds": self.stats.elapsed,
                "eta_seconds": self.stats.eta_seconds,
                "recent_logs": list(self.logs),
            }

    # --- Reporter Interface Implementation -----------------------------------

    def start(self, stats: RunStats) -> None:
        self.stats = stats
        self.current_phase = "Starting browser & preparing search plan"
        self._append_log("Scraping job started.")

    def stop(self) -> None:
        self.current_phase = "Completed"
        self._append_log("Scraping run finished.")

    def on_phase(self, message: str) -> None:
        self.current_phase = message
        self._append_log(message)

    def on_plan(self, units: list[SearchUnit]) -> None:
        count = len(units)
        self.current_phase = f"Planned {count} search units"
        self._append_log(f"Generated search plan with {count} search units.")

    def on_unit_start(self, unit: SearchUnit, index: int, total: int) -> None:
        self.current_unit_label = unit.label
        self.unit_done = 0
        self.unit_total = 0
        self.current_phase = f"Searching unit {index}/{total}: {unit.label}"
        self._append_log(f"Starting search unit ({index}/{total}): {unit.label}")

    def on_unit_progress(self, done: int, total: int) -> None:
        self.unit_done = done
        self.unit_total = total
        self._notify()

    def on_unit_end(self, unit: SearchUnit, found: int, unique: int, qualified: int) -> None:
        self._append_log(
            f"Finished unit '{unit.label}': {found} found, {unique} unique, {qualified} qualified."
        )

    def on_business(self, business, unique: bool, qualified: bool) -> None:
        name = getattr(business, "name", "Business")
        if unique:
            status_desc = "Qualified lead" if qualified else "Unique business"
            self._append_log(f"Found: {name} ({status_desc})")
        self._notify()

    def on_log(self, message: str, level: str = "info") -> None:
        self._append_log(message, level=level)

    def refresh(self) -> None:
        self._notify()
