# app/tests/test_config.py

import os
from unittest.mock import patch

from app.config import Settings


def test_config_defaults():
    """Ensure default values load correctly when no env vars provided."""
    s = Settings()

    assert s.DB_USER == "root"
    assert s.DB_PASSWORD == "Mypass123"
    assert s.DB_HOST == "127.0.0.1"
    assert s.DB_PORT == 3306
    assert s.DB_NAME == "syslog_new"
    assert s.DB_POOL_NAME == "syslog_pool"
    assert s.DB_POOL_SIZE == 5

    assert s.SYSLOG_PORT == 6666
    assert s.SIMILARITY_THRESHOLD == 0.6
    assert s.LOG_LEVEL == "INFO"

    assert s.SPRINGBOOT_HOST == "localhost"
    assert s.SPRINGBOOT_PORT == 8080


def test_config_env_override():
    """Verify environment variables override default values."""
    env = {
        "DB_USER": "admin",
        "DB_PASSWORD": "Secret!",
        "DB_HOST": "db.example.com",
        "DB_PORT": "9999",   # ensure int conversion
        "DB_POOL_SIZE": "20",
        "SYSLOG_PORT": "7777",
        "SIMILARITY_THRESHOLD": "0.85",
        "SPRINGBOOT_HOST": "remote",
        "SPRINGBOOT_PORT": "9090",
    }

    with patch.dict(os.environ, env, clear=True):
        s = Settings()

        assert s.DB_USER == "admin"
        assert s.DB_PASSWORD == "Secret!"
        assert s.DB_HOST == "db.example.com"
        assert s.DB_PORT == 9999
        assert s.DB_POOL_SIZE == 20

        assert s.SYSLOG_PORT == 7777
        assert s.SIMILARITY_THRESHOLD == 0.85

        assert s.SPRINGBOOT_HOST == "remote"
        assert s.SPRINGBOOT_PORT == 9090


def test_config_partial_env_override():
    """Ensure unspecified values remain defaults."""
    with patch.dict(os.environ, {"DB_USER": "xyz"}, clear=True):
        s = Settings()

        assert s.DB_USER == "xyz"          # overridden
        assert s.DB_NAME == "syslog_new"   # default stays
        assert s.SYSLOG_PORT == 6666       # default stays


def test_config_type_enforcement():
    """Settings should cast values into correct types."""
    with patch.dict(os.environ, {
        "DB_PORT": "1234",
        "DB_POOL_SIZE": "10",
        "SIMILARITY_THRESHOLD": "0.42",
        "SPRINGBOOT_PORT": "5050",
    }, clear=True):
        s = Settings()

        assert isinstance(s.DB_PORT, int)
        assert isinstance(s.DB_POOL_SIZE, int)
        assert isinstance(s.SIMILARITY_THRESHOLD, float)
        assert isinstance(s.SPRINGBOOT_PORT, int)

        assert s.DB_PORT == 1234
        assert s.DB_POOL_SIZE == 10
        assert s.SIMILARITY_THRESHOLD == 0.42
        assert s.SPRINGBOOT_PORT == 5050
