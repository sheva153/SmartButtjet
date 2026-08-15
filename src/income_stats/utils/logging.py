"""Application-owned Loguru sink configuration."""

import sys
from contextlib import suppress
from pathlib import Path
from threading import Lock

from loguru import logger

_SINK_LOCK = Lock()
_SINK_IDS: list[int] = []
_DEFAULT_SINK_REMOVED = False


def configure_logging(
    level: str = "INFO",
    log_file: Path = Path("logs/bot.log"),
) -> None:
    """Install one console sink and one rotating debug-file sink.

    Reconfiguration removes only sinks installed by this function. The
    process-level Loguru default sink is removed once so application messages
    are not duplicated on stderr.
    """
    global _DEFAULT_SINK_REMOVED

    with _SINK_LOCK:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        replacement_sink_ids: list[int] = []
        try:
            replacement_sink_ids.append(
                logger.add(
                    sys.stderr,
                    level=level,
                    backtrace=False,
                    diagnose=False,
                )
            )
            replacement_sink_ids.append(
                logger.add(
                    log_file,
                    level="DEBUG",
                    rotation="10 MB",
                    retention="14 days",
                    compression="zip",
                    backtrace=False,
                    diagnose=False,
                )
            )
        except Exception:
            for sink_id in replacement_sink_ids:
                with suppress(ValueError):
                    logger.remove(sink_id)
            raise

        if not _DEFAULT_SINK_REMOVED:
            with suppress(ValueError):
                logger.remove(0)
            _DEFAULT_SINK_REMOVED = True

        for sink_id in _SINK_IDS:
            with suppress(ValueError):
                logger.remove(sink_id)
        _SINK_IDS[:] = replacement_sink_ids
