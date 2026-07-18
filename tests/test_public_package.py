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
