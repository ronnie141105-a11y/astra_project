"""
Flask app assembly (Milestone 8).

Per design review OQ-1(A)/OQ-2(A): a minimal local Flask app, running in
the same process as `main.py`'s poll loop (a background thread), reading
the latest cycle from a `CycleStore` the poll loop updates. This module
is a thin wrapper around Flask itself -- `astra.dashboard.routes` owns
the actual endpoints, `astra.dashboard.serializers` owns the JSON shape.
"""

import logging
import os
import threading
from typing import Optional

from flask import Flask

from astra.dashboard.routes import build_blueprint
from astra.dashboard.scenario_routes import build_scenario_blueprint
from astra.dashboard.store import CycleStore
from astra.interface.state_reader import StateReader
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger("astra.dashboard")

#: Where the Scenario Builder page saves/loads named scenarios. Kept
#: separate from `scenarios/*.scn` (the BlueSky-format demo files) so
#: the builder's JSON files never collide with those.
_DEFAULT_SCENARIOS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scenarios", "builder"
)


def create_app(
    store: CycleStore,
    config: ASTRAConfig,
    reader: Optional[StateReader] = None,
    scenarios_dir: str = _DEFAULT_SCENARIOS_DIR,
) -> Flask:
    """Build the Flask app: register the dashboard's (and, if a `reader`
    is given, the Scenario Builder's) blueprints.

    Args:
        store: The `CycleStore` the poll loop publishes cycles into.
        config: The running `ASTRAConfig`.
        reader: The live `StateReader` the poll loop is reading from.
            Optional and defaults to `None` for backwards compatibility
            (existing callers/tests that only need the read-only `/state`
            endpoint). When given, the Scenario Builder's `/scenario*`
            routes and its `/scenario` page are also registered.
        scenarios_dir: Where saved Scenario Builder scenarios live.

    Returns:
        A configured `Flask` app, not yet running.
    """
    dashboard_dir = os.path.dirname(__file__)
    app = Flask(__name__, template_folder=dashboard_dir, static_folder=dashboard_dir)
    app.register_blueprint(build_blueprint(store, config))
    if reader is not None:
        app.register_blueprint(build_scenario_blueprint(reader, scenarios_dir))
    # Werkzeug's own request logging is noisy at INFO for a 1 Hz poll
    # loop being hit by a 1 Hz frontend; the astra.dashboard logger above
    # already reports startup, so quiet the per-request access log.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    return app


def run_dashboard_in_background(
    store: CycleStore, config: ASTRAConfig, reader: Optional[StateReader] = None
) -> threading.Thread:
    """Start the dashboard's Flask dev server in a daemon thread.

    `main.py` calls this once, then runs its poll loop in the main
    thread as before -- so `Ctrl+C` still stops the process the same
    way it always has (the dashboard thread is `daemon=True` and exits
    with the process, it is never joined).

    Args:
        store: The `CycleStore` the poll loop will publish cycles into.
        config: The running `ASTRAConfig` (`dashboard_host`/`dashboard_port`).
        reader: The same `StateReader` instance `main.py`'s poll loop
            calls `.poll()` on. Passed through so the Scenario Builder's
            routes (running on Flask's request thread) can create/edit/
            delete aircraft and pause/resume/step/reset the *same*
            simulation the poll loop is advancing -- not a copy of it.
            If omitted, the Scenario Builder page is not registered.

    Returns:
        The started `threading.Thread` (already running), in case the
        caller wants to inspect `.is_alive()`.
    """
    app = create_app(store, config, reader=reader)

    def _serve() -> None:
        app.run(
            host=config.dashboard_host,
            port=config.dashboard_port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )

    thread = threading.Thread(target=_serve, name="astra-dashboard", daemon=True)
    thread.start()
    _LOG.info(
        "Dashboard live at http://%s:%d/ (updates every %.1fs)",
        config.dashboard_host,
        config.dashboard_port,
        config.poll_interval_s,
    )
    return thread
