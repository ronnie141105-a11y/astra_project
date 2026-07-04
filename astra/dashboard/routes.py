"""
HTTP routes (Milestone 8).

Deliberately thin: every handler below does exactly two things --
read the latest `DashboardSnapshot` from the `CycleStore` it was built
with, and hand it to `astra.dashboard.serializers`. No engine, no
`Pipeline`, and no domain object is imported here. This is the "clean
API boundary" the design review asks for (see docs/milestone_8_dashboard.md
"Clean API boundary for BlueSky live mode / RL"): a future live-BlueSky
run or a future RL-based `ResolutionEngine` replacement changes what
`CycleStore` gets updated with, never this file.
"""

from flask import Blueprint, Response, jsonify, render_template

from astra.dashboard import serializers
from astra.dashboard.store import CycleStore
from astra.utils.config import ASTRAConfig


def build_blueprint(store: CycleStore, config: ASTRAConfig) -> Blueprint:
    """Build the dashboard's Flask `Blueprint`, bound to one store/config.

    A factory (rather than module-level routes) so `astra.dashboard.server`
    can construct a fresh app per `Pipeline`/`CycleStore` instance --
    important for `tests/test_dashboard.py`, which builds its own
    isolated store per test rather than sharing global state.

    Args:
        store: The `CycleStore` `main.py`'s poll loop publishes into.
        config: The running `ASTRAConfig` (for `poll_interval_s` and the
            Phase 8 display-cap fields, read only via `serializers`).

    Returns:
        A `flask.Blueprint` ready to register on an `Flask` app.
    """
    blueprint = Blueprint("dashboard", __name__)

    @blueprint.route("/")
    def index() -> str:
        """Serve the single-page HMI shell (map + table + timeline panels)."""
        return render_template(
            "index.html",
            poll_interval_s=config.poll_interval_s,
            dashboard_host=config.dashboard_host,
            dashboard_port=config.dashboard_port,
        )

    @blueprint.route("/state")
    def state() -> Response:
        """Return the latest `DashboardSnapshot` as JSON.

        Polled by the frontend every `poll_interval_s` (design review
        OQ-5(B)) -- see `static/js/dashboard.js`.
        """
        payload = serializers.serialize_dashboard_snapshot(store.snapshot(), config)
        return jsonify(payload)

    return blueprint
