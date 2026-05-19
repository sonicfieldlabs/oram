"""oram.logging — module-level logger configuration.

§7.3: centralized logging for the oram project.
all modules should use `from oram.logging import logger` instead of print().
"""

from __future__ import annotations

import logging
import os
import sys

# create the oram logger
logger = logging.getLogger("oram")

# default to WARNING unless ORAM_LOG_LEVEL or -v is set
_level = os.environ.get("ORAM_LOG_LEVEL", "WARNING").upper()
logger.setLevel(getattr(logging, _level, logging.WARNING))

# handler: stderr with a clean format
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname).1s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(_handler)


def set_verbosity(verbose: bool = False, quiet: bool = False) -> None:
    """configure log level from CLI flags."""
    if quiet:
        logger.setLevel(logging.ERROR)
    elif verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
