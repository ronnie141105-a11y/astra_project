"""
Shared logging configuration.

A small helper rather than `logging.basicConfig()` scattered across
modules, so log formatting is consistent and is configured exactly once
regardless of which module is imported first.
"""

import logging
import sys

_CONFIGURED = False


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a configured logger for the given module name.

    The root ASTRA logging configuration (handler + formatter) is applied
    lazily, exactly once per process, the first time this function is
    called. Subsequent calls only create/return the named child logger.

    Args:
        name: Usually the caller's `__name__`, so log lines are traceable
            to the emitting module (e.g. "astra.interface.state_reader").
        level: Logging level for this specific logger. Defaults to INFO.

    Returns:
        A standard library `logging.Logger` instance.
    """
    global _CONFIGURED
    if not _CONFIGURED:
        handler = logging.StreamHandler(stream=sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        root = logging.getLogger("astra")
        root.addHandler(handler)
        root.setLevel(level)
        _CONFIGURED = True

    return logging.getLogger(name)
