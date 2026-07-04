"""
Dashboard read-model (Milestone 8).

`DashboardSnapshot` is the dashboard's *own* small domain type -- it is
not another copy of the Milestone 2-7 pipeline's data model. It just
answers "what does the dashboard currently have to show", wrapping a
`CycleResult` (or `None`, before the first cycle completes) with the
bookkeeping (`cycle_count`, `updated_at_s`) a live HMI needs to report
staleness. See `astra.dashboard.store.CycleStore`, which owns the one
mutable instance of this per process, and `astra.dashboard.serializers`,
which turns it into JSON.
"""

from dataclasses import dataclass
from typing import Optional

from astra.pipeline import CycleResult


@dataclass(frozen=True)
class DashboardSnapshot:
    """An immutable, point-in-time view of what the dashboard can show.

    Attributes:
        cycle_result: The most recently completed `Pipeline.run_cycle()`
            result, or `None` if no cycle has completed yet (e.g. the
            dashboard was opened before the first `poll()` returned
            traffic).
        cycle_count: Total number of cycles run so far this process
            (monotonic; not reset when tracks close). Lets the frontend
            detect "no new cycle since last poll" without comparing
            floating-point timestamps.
        updated_at_s: Simulation time (`snapshot.timestamp_s`) of
            `cycle_result`, or `None` if `cycle_result` is `None`.
    """

    cycle_result: Optional[CycleResult]
    cycle_count: int
    updated_at_s: Optional[float]

    @classmethod
    def empty(cls) -> "DashboardSnapshot":
        """The initial snapshot before any pipeline cycle has run."""
        return cls(cycle_result=None, cycle_count=0, updated_at_s=None)
