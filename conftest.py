"""
Shared pytest fixtures for the ASTRA test suite (tests/pytest/).

Kept at the repo root (rather than inside tests/) so pytest's rootdir
detection and `python_files`/`testpaths` resolution in pytest.ini stay
simple, and so a future `tests/pytest/subpkg/conftest.py` can add
narrower fixtures without shadowing anything unexpectedly.
"""

import sys
from pathlib import Path

import pytest

# tests/ (legacy Runner-style scripts) each insert the repo root onto
# sys.path themselves; pytest-collected tests import `astra` as an
# installed-looking package, so make sure the repo root is importable
# the same way here, once, for the whole session.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from astra.utils.config import ASTRAConfig  # noqa: E402


@pytest.fixture
def config() -> ASTRAConfig:
    """A default `ASTRAConfig`, unchanged from the project's defaults.

    Most tests don't care about specific tuning constants; they just
    need *a* valid, internally-consistent config to construct engines,
    connectors, and the pipeline with.
    """
    return ASTRAConfig()


@pytest.fixture
def fast_mock_config() -> ASTRAConfig:
    """An `ASTRAConfig` with a short poll interval, for streaming tests.

    Keeps async streaming tests (which actually sleep between frames)
    fast without needing every test to compute/override this by hand.
    """
    import dataclasses

    return dataclasses.replace(ASTRAConfig(), poll_interval_s=0.05)


@pytest.fixture
def seeded_mock_connector():
    """A connected, running `MockConnector` with a few converging aircraft.

    Mirrors `main.py`'s `_setup_mock_traffic`, at a smaller scale, so
    pipeline/streaming tests exercise realistic multi-aircraft input
    without duplicating scenario-building logic in every test module.
    """
    from astra.interface.mock_connector import MockConnector

    connector = MockConnector(sim_step_s=1.0)
    connector.connect()
    connector.create_aircraft("AC1", "A320", 10.90, 106.70, 180.0, 30000, 250)
    connector.create_aircraft("AC2", "B738", 10.70, 106.70, 0.0, 30000, 250)
    connector.send_command("OP")
    return connector
