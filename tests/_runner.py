"""
Minimal, dependency-free assertion runner shared by the `tests/` scripts.

The repository does not depend on `pytest` (see `requirements.txt` — kept
deliberately minimal for a thesis-scale prototype), and Phase 1/2
verification was likewise done with plain `assert`-based scripts (see
`Developer_Handover.md` "Running the verification suite"). This module
formalises that same style into a small reusable helper so Milestone 3+
regression scripts don't each hand-roll their own pass/fail counting.

Usage::

    from tests._runner import Runner

    r = Runner("Phase 3 — Cluster detection")
    r.check("two aircraft 5NM apart form one cluster", len(clusters) == 1)
    r.check_close("centroid latitude", cluster.centroid_lat, 47.05, tol=1e-6)
    r.summary()   # prints "N/N checks PASS" and exits(1) on any failure
"""

import sys
from typing import Optional


class Runner:
    """Accumulates named pass/fail checks and prints a final summary."""

    def __init__(self, title: str) -> None:
        """Start a new check group.

        Args:
            title: Human-readable name printed as a banner, e.g.
                ``"Phase 3 — Cluster detection"``.
        """
        self._title = title
        self._passed = 0
        self._failed = 0
        print("=" * 78)
        print(f"  {title}")
        print("=" * 78)

    def check(self, description: str, condition: bool) -> None:
        """Record one boolean check.

        Args:
            description: Short description of what was checked, printed
                next to the PASS/FAIL marker.
            condition: The check's result. Truthy = pass.
        """
        if condition:
            self._passed += 1
            print(f"  [PASS] {description}")
        else:
            self._failed += 1
            print(f"  [FAIL] {description}")

    def check_close(
        self,
        description: str,
        actual: float,
        expected: float,
        tol: float = 1e-6,
    ) -> None:
        """Record one numerical closeness check.

        Args:
            description: Short description of what was checked.
            actual: The computed value.
            expected: The expected value.
            tol: Maximum allowed absolute difference.
        """
        diff = abs(actual - expected)
        condition = diff <= tol
        detail = f"{description} (actual={actual:.6g}, expected={expected:.6g}, diff={diff:.2e})"
        self.check(detail, condition)

    def check_raises(
        self,
        description: str,
        callable_,
        exception_type: type,
    ) -> None:
        """Record a check that ``callable_()`` raises ``exception_type``.

        Args:
            description: Short description of what was checked.
            callable_: A zero-argument callable expected to raise.
            exception_type: The exception type expected to be raised.
        """
        try:
            callable_()
            self.check(description, False)
        except exception_type:
            self.check(description, True)
        except Exception as exc:  # pragma: no cover - diagnostic aid
            self.check(f"{description} (wrong exception type: {type(exc).__name__})", False)

    def summary(self) -> Optional[int]:
        """Print the final PASS/FAIL tally and exit(1) if anything failed.

        Returns:
            ``None`` (this method calls ``sys.exit`` on failure and never
            returns in that case; a return value is provided only so
            callers may call this as the last line of a script uniformly).
        """
        total = self._passed + self._failed
        print("-" * 78)
        print(f"  {self._title}: {self._passed}/{total} checks PASS")
        print("=" * 78)
        print()
        if self._failed:
            sys.exit(1)
        return None
