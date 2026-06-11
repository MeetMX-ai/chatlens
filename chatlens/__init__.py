import logging

logger = logging.getLogger(__name__)

try:
    from importlib.metadata import version

    __version__ = version("chatlens")
except Exception:
    __version__ = "1.0.0"
    logger.debug("Failed to get version from importlib.metadata, using fallback")
