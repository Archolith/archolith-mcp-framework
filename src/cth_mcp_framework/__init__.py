"""Backward-compatible imports for the former cth.mcp.framework package."""

from __future__ import annotations

import importlib
import sys

from archolith_mcp_framework import *  # noqa: F403
from archolith_mcp_framework import __all__

_SUBMODULES = (
    "base",
    "duration_stats",
    "jobs",
    "middleware",
    "response",
    "runner",
    "server",
    "transforms",
    "mixins",
    "mixins.audit",
    "mixins.chunked_io",
    "mixins.compact",
    "mixins.git",
    "mixins.job_control",
    "mixins.paths",
)

for _submodule in _SUBMODULES:
    sys.modules[f"{__name__}.{_submodule}"] = importlib.import_module(
        f"archolith_mcp_framework.{_submodule}"
    )
