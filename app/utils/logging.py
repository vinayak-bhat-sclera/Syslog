# app/utils/logging.py
import logging
import sys

from app.config import settings

def configure_logging():
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)s %(name)s - %(message)s"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger()
    root.setLevel(log_level)
    if not root.handlers:
        root.addHandler(handler)
    else:
        # replace handlers to ensure consistent formatting
        root.handlers = [handler]
