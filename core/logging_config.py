"""
Logging configuration helper for production vs development separation.
"""
import logging
from core.config import settings

def is_development() -> bool:
    """Check if running in development environment."""
    return settings.ENVIRONMENT.lower() == "development" or settings.DEBUG

def should_log_debug() -> bool:
    """Check if debug logging should be enabled."""
    return is_development()

def should_take_screenshots() -> bool:
    """Check if screenshots should be taken (only in development)."""
    return is_development()

def get_logger(name: str) -> logging.Logger:
    """Get a logger with appropriate configuration."""
    logger = logging.getLogger(name)
    
    # Set level based on environment
    if is_development():
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    
    return logger
