import logging
import sys
from src.config import LOG_LEVEL

def get_logger(name: str) -> logging.Logger:
    """
    Creates a configured logger instance for the given module name.
    All output goes to stderr to avoid corrupting MCP's stdio JSON stream.
    """
    logger = logging.getLogger(f"vex.{name}")
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        logger.propagate = False
    
    return logger
