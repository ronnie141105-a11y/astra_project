"""
In-process pipeline<->dashboard bridge (Milestone 8, OQ-2).

Per the approved design review, `main.py` stays the single live-loop
owner (exactly the role it already had for Milestone 1) and now runs
the full Milestone 2-7 `Pipeline` every `poll_interval_s`. The
dashboard's Flask server runs in the same process (a background
thread) and reads the latest cycle's results from this small in-memory
store -- no IPC, no second process, no database.

`CycleStore` is the *only* new concurrency primitive this milestone
introduces: a lock around "the last `CycleResult`", because one thread
(the poll loop) writes it and another (Flask's request-handling thread)
reads it. Everything else in `astra.dashboard` is single-threaded.
"""

import threading

from astra.dashboard.models import DashboardSnapshot
from astra.pipeline import CycleResult


class CycleStore:
    """Holds the latest `CycleResult`, safe for one writer + many readers.

    Example::

        store = CycleStore()
        # poll loop thread:
        store.update(pipeline.run_cycle(snapshot))
        # Flask request-handling thread(s):
        snapshot = store.snapshot()
    """

    def __init__(self) -> None:
        """Start with an empty snapshot (no cycle has run yet)."""
        self._lock = threading.Lock()
        self._snapshot = DashboardSnapshot.empty()

    def update(self, cycle_result: CycleResult) -> None:
        """Publish a newly-completed cycle. Called once per poll cycle.

        Args:
            cycle_result: The result of the `Pipeline.run_cycle()` call
                that just completed.
        """
        with self._lock:
            self._snapshot = DashboardSnapshot(
                cycle_result=cycle_result,
                cycle_count=self._snapshot.cycle_count + 1,
                updated_at_s=cycle_result.snapshot.timestamp_s,
            )

    def snapshot(self) -> DashboardSnapshot:
        """Return the latest published `DashboardSnapshot`.

        Returns:
            `DashboardSnapshot.empty()` if `update()` has never been
            called (no cycle has completed yet).
        """
        with self._lock:
            return self._snapshot
