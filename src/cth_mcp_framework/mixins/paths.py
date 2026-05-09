"""PathValidationMixin — safe path resolution for cth.* MCP servers.

Extracted from artifact_gateway.py. Provides:
- resolve_safe_path(): rejects traversal, absolute paths, separators
- validate_filename(): optional regex match (e.g. wrapup naming)
- validate_glob_pattern(): safe basename-only globs
"""

from __future__ import annotations

import re
from pathlib import Path


class PathValidationMixin:
    """Mixin for path validation in cth.* MCP servers.

    Compose with BaseGatewayServer via multiple inheritance::

        class MyServer(BaseGatewayServer, PathValidationMixin):
            ...
    """

    def resolve_safe_path(self, base_dir: Path, filename: str) -> Path:
        """Resolve a filename under base_dir and validate it stays contained.

        Rejects absolute paths, path traversal (..), and directory separators.

        Args:
            base_dir: The allowed root directory.
            filename: A bare filename (no path components).

        Returns:
            The resolved absolute Path.

        Raises:
            ValueError: If the filename escapes base_dir.
        """
        if not filename:
            raise ValueError("Filename cannot be empty")

        # Check for absolute paths (platform-specific and explicit checks)
        if Path(filename).is_absolute():
            raise ValueError("Absolute paths not allowed: %s" % filename)
        # Unix-style leading slash (not detected on Windows)
        if filename.startswith("/"):
            raise ValueError("Absolute paths not allowed: %s" % filename)
        # Windows drive letter
        if len(filename) >= 2 and filename[1] == ":":
            raise ValueError("Absolute paths not allowed: %s" % filename)

        # Path traversal
        if ".." in filename:
            raise ValueError("Path traversal not allowed: %s" % filename)
        if "/" in filename or "\\" in filename:
            raise ValueError("Directory separators not allowed: %s" % filename)

        full_path = (base_dir / filename).resolve()

        # Ensure resolved path is still within base_dir
        try:
            full_path.relative_to(base_dir.resolve())
        except ValueError:
            raise ValueError("Path escapes allowed directory: %s" % filename)

        return full_path

    def validate_filename(
        self,
        filename: str,
        pattern: re.Pattern[str] | None = None,
    ) -> None:
        """Validate a filename for safety and optional regex match.

        Args:
            filename: The bare filename to validate.
            pattern: Optional compiled regex the filename must match.

        Raises:
            ValueError: If the filename is invalid or doesn't match the pattern.
        """
        if not filename:
            raise ValueError("Filename cannot be empty")

        # Absolute paths
        if Path(filename).is_absolute():
            raise ValueError("Absolute paths not allowed: %s" % filename)
        if filename.startswith("/"):
            raise ValueError("Absolute paths not allowed: %s" % filename)
        if len(filename) >= 2 and filename[1] == ":":
            raise ValueError("Absolute paths not allowed: %s" % filename)

        # Path traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            raise ValueError("Path traversal not allowed: %s" % filename)

        # Optional pattern match
        if pattern is not None and not pattern.match(filename):
            raise ValueError(
                "Filename %r does not match required pattern %s"
                % (filename, pattern.pattern)
            )

    def validate_glob_pattern(self, pattern: str | None) -> str:
        """Validate and sanitize a glob pattern.

        Only allows simple basename patterns (e.g. '*.md', 'WRAPUP-*.md').
        Rejects path traversal or directory components.

        Args:
            pattern: The glob pattern to validate, or None for default.

        Returns:
            Sanitized pattern string (default '*' if None/empty).

        Raises:
            ValueError: If pattern contains path traversal or invalid characters.
        """
        if not pattern:
            return "*"

        # Reject absolute paths
        if Path(pattern).is_absolute():
            raise ValueError("Pattern cannot be an absolute path: %s" % pattern)
        if pattern.startswith("/"):
            raise ValueError("Pattern cannot be an absolute path: %s" % pattern)
        if len(pattern) >= 2 and pattern[1] == ":":
            raise ValueError("Pattern cannot be an absolute path: %s" % pattern)

        # Reject path traversal
        if ".." in pattern:
            raise ValueError(
                "Pattern cannot contain path traversal '..': %s" % pattern
            )
        if "/" in pattern or "\\" in pattern:
            raise ValueError(
                "Pattern cannot contain directory separators: %s" % pattern
            )

        # Only allow safe glob characters
        safe_chars = set(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789.*?!_-[]"
        )
        for char in pattern:
            if char not in safe_chars:
                raise ValueError(
                    "Pattern contains invalid character '%s': %s" % (char, pattern)
                )

        return pattern
