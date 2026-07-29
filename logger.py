"""
Structured logging for the Predict.fun bot.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from config import Config


class BotLogger:
    """Wraps Python logging with a consistent format and file rotation."""

    _instance: Optional["BotLogger"] = None

    def __init__(self, config: Config):
        self.logger = logging.getLogger("predict_bot")
        self.logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

        # Avoid duplicate handlers on re-init
        if self.logger.handlers:
            return

        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Console handler
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        self.logger.addHandler(console)

        # File handler with rotation
        file_handler = RotatingFileHandler(
            config.log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,
        )
        file_handler.setFormatter(fmt)
        self.logger.addHandler(file_handler)

    @classmethod
    def get(cls, config: Optional[Config] = None) -> "BotLogger":
        if cls._instance is None:
            cls._instance = cls(config or Config())
        return cls._instance

    @property
    def log(self) -> logging.Logger:
        return self.logger


def get_logger(name: str = "predict_bot") -> logging.Logger:
    """Get a child logger under the main bot logger."""
    if name == "predict_bot":
        return logging.getLogger("predict_bot")
    return logging.getLogger(f"predict_bot.{name}")
