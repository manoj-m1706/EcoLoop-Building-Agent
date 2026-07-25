import sys
from pathlib import Path
from loguru import logger
from config import Config

# Ensure logs directory exists
Config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
log_file_path = Config.LOGS_DIR / "simulation.log"

# Remove default logger config
logger.remove()

# Add console logging with formatting and colors
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",
    enqueue=True
)

# Add file logging with rotation and retention
logger.add(
    str(log_file_path),
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    level="DEBUG",
    rotation="10 MB",
    retention="30 days",
    compression="zip",
    enqueue=True
)

logger.info("Logging system initialized. Writing logs to {}", log_file_path)
