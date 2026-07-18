"""ChunkedIOMixin — bounded UTF-8 chunk reading for MCP servers.

Extracted from artifact_gateway.py. Provides:
- read_chunk(): UTF-8 safe chunk read by byte offset
- validate_chunk_window(): clamp/validate chunk parameters
- Constants: DEFAULT_CHUNK_BYTES, MAX_CHUNK_BYTES
"""

from __future__ import annotations

from pathlib import Path


class ChunkedIOMixin:
    """Mixin for chunked I/O in MCP servers.

    Compose with BaseGatewayServer via multiple inheritance::

        class MyServer(BaseGatewayServer, ChunkedIOMixin):
            ...
    """

    DEFAULT_CHUNK_BYTES: int = 12_000
    MAX_CHUNK_BYTES: int = 32_000

    def read_chunk(
        self,
        file_path: Path,
        offset: int,
        length: int,
    ) -> tuple[str, int]:
        """Read a UTF-8 chunk by byte offset without returning a split code point.

        The returned byte count may be smaller than requested when the
        requested boundary lands in the middle of a multibyte character.

        Args:
            file_path: Absolute path to the file.
            offset: Byte offset to start reading from.
            length: Maximum bytes to read.

        Returns:
            Tuple of (decoded_text, bytes_actually_read).

        Raises:
            ValueError: If offset exceeds file size or chunk starts mid-character.
        """
        data = file_path.read_bytes()
        total_size = len(data)

        if offset > total_size:
            raise ValueError(
                "Offset %d exceeds file size %d" % (offset, total_size)
            )

        end = min(offset + length, total_size)
        chunk = data[offset:end]

        # Trim trailing bytes that split a multibyte UTF-8 character
        while chunk:
            try:
                return chunk.decode("utf-8"), len(chunk)
            except UnicodeDecodeError:
                chunk = chunk[:-1]

        if offset == total_size:
            return "", 0

        raise ValueError("Chunk starts in the middle of a UTF-8 character")

    def validate_chunk_window(
        self,
        offset: int,
        length: int | None = None,
    ) -> int:
        """Validate chunk offsets and normalize length to a safe byte count.

        Args:
            offset: Byte offset (must be >= 0).
            length: Maximum bytes, or None for DEFAULT_CHUNK_BYTES.

        Returns:
            Validated length value.

        Raises:
            ValueError: If offset or length is out of range.
        """
        if not isinstance(offset, int):
            raise ValueError("Offset must be an integer")
        if offset < 0:
            raise ValueError("Offset cannot be negative")

        if length is None:
            return self.DEFAULT_CHUNK_BYTES
        if not isinstance(length, int):
            raise ValueError("Length must be an integer")
        if length <= 0:
            raise ValueError("Length must be positive")
        if length > self.MAX_CHUNK_BYTES:
            raise ValueError(
                "Length cannot exceed %d bytes" % self.MAX_CHUNK_BYTES
            )

        return length
