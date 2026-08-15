"""Temporary artifact lifecycle helpers."""

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


@contextmanager
def temporary_artifacts(*paths: Path) -> Iterator[tuple[Path, ...]]:
    """Remove every supplied file when its send/use scope ends."""
    try:
        yield paths
    finally:
        for path in paths:
            with suppress(OSError):
                path.unlink(missing_ok=True)
