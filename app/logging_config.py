import sys
from loguru import logger
from app.config import settings

def mask_api_keys(record):
    """
    Masks full API keys in the log record message.
    Replaces complete API keys with their masked version (first 8 characters + hash).
    """
    message = record["message"]
    for key in settings.api_keys_list:
        if len(key) > 8 and key in message:
            masked = f"{key[:8]}***"
            message = message.replace(key, masked)
    record["message"] = message
    return True

def setup_logging():
    # Clear existing handlers
    logger.remove()
    
    # Add stderr handler with masking filter
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        filter=mask_api_keys
    )
    
    # Add file handler
    logger.add(
        "logs/app.log",
        rotation="10 MB",
        retention="14 days",
        level=settings.LOG_LEVEL,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        filter=mask_api_keys
    )
    
    logger.info("Logging configured successfully.")

# Initialize logging on import
setup_logging()
