"""Public package naming and legacy-import compatibility tests."""

import archolith_mcp_framework
import cth_mcp_framework
from archolith_mcp_framework import server as public_server
from cth_mcp_framework import server as legacy_server


def test_primary_package_exports_gateway_factory() -> None:
    assert callable(archolith_mcp_framework.create_gateway_server)


def test_legacy_import_preserves_public_exports() -> None:
    assert cth_mcp_framework.create_gateway_server is archolith_mcp_framework.create_gateway_server


def test_legacy_submodule_forwards_to_primary_module() -> None:
    assert legacy_server is public_server


def test_legacy_shim_forwards_every_public_submodule() -> None:
    """Every module of the primary package must be reachable under the legacy name.

    The shim's _SUBMODULES tuple is hand-maintained, and the existing tests
    spot-check one entry ('server'). That asserts the shim works by example
    rather than asserting it is COMPLETE, so a module added to
    archolith_mcp_framework without a matching _SUBMODULES entry silently
    stops resolving as cth_mcp_framework.<name> for every existing consumer.
    This walks the real package instead, so the omission fails here rather
    than in a downstream server at import time.
    """
    import importlib
    import pkgutil

    import archolith_mcp_framework as primary

    missing = []
    for info in pkgutil.walk_packages(primary.__path__, prefix=""):
        name = info.name
        try:
            expected = importlib.import_module(f"archolith_mcp_framework.{name}")
        except ImportError:  # optional/extra dependency, not a shim gap
            continue
        try:
            legacy = importlib.import_module(f"cth_mcp_framework.{name}")
        except ImportError:
            missing.append(name)
            continue
        if legacy is not expected:
            missing.append(name)

    assert not missing, (
        "legacy shim does not forward these submodules; add them to "
        "cth_mcp_framework._SUBMODULES: %s" % sorted(missing)
    )
