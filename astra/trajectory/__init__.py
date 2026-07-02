"""
Trajectory prediction (Phase 2 - not yet implemented).

Will consume `astra.interface.traffic_state.TrafficSnapshot` objects and
produce predicted future AircraftState objects at the horizons configured
in `ASTRAConfig.prediction_horizons_min`, using a simplified kinematic
model (straight-line extrapolation from position, heading and speed).
"""
