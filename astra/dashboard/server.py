"""
Flask app assembly (Milestone 8).

Per design review OQ-1(A)/OQ-2(A): a minimal local Flask app, running in
the same process as `main.py`'s poll loop (a background thread), reading
the latest cycle from a `CycleStore` the poll loop updates. This module
is a thin wrapper around Flask itself -- `astra.dashboard.routes` owns
the actual endpoints, `astra.dashboard.serializers` owns the JSON shape.
"""

import logging
import threading

from flask import Flask

from astra.dashboard.routes import build_blueprint
from astra.dashboard.store import CycleStore
from astra.utils.config import ASTRAConfig
from astra.utils.logger import get_logger

_LOG = get_logger("astra.dashboard")


def create_app(store: CycleStore, config: ASTRAConfig) -> Flask:
    """Build the Flask app: register `astra.dashboard.routes`'s blueprint.

    Args:
        store: The `CycleStore` the poll loop publishes cycles into.
        config: The running `ASTRAConfig`.

    Returns:
        A configured `Flask` app, not yet running.
    """
    app = Flask(__name__)
    app.register_blueprint(build_blueprint(store, config))
    # Werkzeug's own request logging is noisy at INFO for a 1 Hz poll
    # loop being hit by a 1 Hz frontend; the astra.dashboard logger above
    # already reports startup, so quiet the per-request access log.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    return app


def run_dashboard_in_background(store: CycleStore, config: ASTRAConfig) -> threading.Thread:
    """Start the dashboard's Flask dev server in a daemon thread.

    `main.py` calls this once, then runs its poll loop in the main
    thread as before -- so `Ctrl+C` still stops the process the same
    way it always has (the dashboard thread is `daemon=True` and exits
    with the process, it is never joined).

    Args:
        store: The `CycleStore` the poll loop will publish cycles into.
        config: The running `ASTRAConfig` (`dashboard_host`/`dashboard_port`).

    Returns:
        The started `threading.Thread` (already running), in case the
        caller wants to inspect `.is_alive()`.
    """
    app = create_app(store, config)

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
