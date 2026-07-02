"""
ASTRA prototype.

A simplified, undergraduate-thesis-scale re-implementation of the SESAR
ASTRA concept (AI-enabled tactical FMP hotspot prediction and resolution).

This package contains ONLY ASTRA logic. BlueSky is treated strictly as an
external traffic simulator process; this code never imports BlueSky's
simulation internals (bluesky.traf, bluesky.sim, ...) outside of the
`astra.interface` package, which acts as the single adapter layer between
BlueSky and the rest of the system (see astra.interface for details).

Sub-packages (mirroring the data flow described in the project brief):

    interface    -> BlueSky connectivity + simulator-agnostic state model
    trajectory   -> kinematic trajectory prediction
    hotspot      -> DBSCAN-based spatial clustering of predicted traffic
    complexity   -> per-hotspot complexity scoring
    prediction   -> hotspot lifecycle prediction (start/end/confidence)
    resolution   -> candidate clearance generation + ranking
    dashboard    -> live visualisation
    utils        -> cross-cutting helpers (config, geodesy, units, logging)
"""
