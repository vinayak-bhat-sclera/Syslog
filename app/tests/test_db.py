# app/tests/test_db.py

import pytest
from unittest.mock import MagicMock, patch

import mysql.connector
from mysql.connector import pooling

from app.db import (
    _init_pool,
    get_db_connection,
    init_db,
    _pool,
)
from app import db as db_module


# -------------------------------------------------------
# Fixture: reset global pool before each test
# -------------------------------------------------------
@pytest.fixture(autouse=True)
def reset_pool():
    db_module._pool = None
    yield
    db_module._pool = None


# -------------------------------------------------------
# _init_pool
# -------------------------------------------------------
def test_init_pool_success():
    fake_pool = MagicMock()
    with patch.object(pooling, "MySQLConnectionPool", return_value=fake_pool):
        _init_pool("testdb")

    assert db_module._pool is fake_pool


def test_init_pool_retry_then_success():
    """First attempt raises error, second works."""
    fake_pool = MagicMock()

    with patch.object(
        pooling,
        "MySQLConnectionPool",
        side_effect=[mysql.connector.Error("fail"), fake_pool],
    ):
        _init_pool("testdb")

    assert isinstance(db_module._pool, MagicMock)


def test_init_pool_fail_after_retries():
    with patch.object(
        pooling, "MySQLConnectionPool", side_effect=mysql.connector.Error("nope")
    ):
        with pytest.raises(mysql.connector.Error):
            _init_pool("testdb")


# -------------------------------------------------------
# get_db_connection()
# -------------------------------------------------------
def test_get_db_connection_success():
    fake_pool = MagicMock()
    fake_conn = MagicMock()

    fake_pool.get_connection.return_value = fake_conn

    with patch.object(pooling, "MySQLConnectionPool", return_value=fake_pool):
        with get_db_connection("db1") as cnx:
            assert cnx is fake_conn

    fake_conn.close.assert_called_once()


def test_get_db_connection_error():
    fake_pool = MagicMock()
    fake_pool.get_connection.side_effect = mysql.connector.Error("boom")

    with patch.object(pooling, "MySQLConnectionPool", return_value=fake_pool):
        with pytest.raises(mysql.connector.Error):
            with get_db_connection("dbX"):
                pass


# -------------------------------------------------------
# init_db()
# -------------------------------------------------------
def test_init_db_success():
    fake_root = MagicMock()
    fake_cursor = MagicMock()
    fake_root.cursor.return_value = fake_cursor

    fake_pool = MagicMock()
    fake_conn = MagicMock()
    fake_cursor2 = MagicMock()
    fake_conn.cursor.return_value = fake_cursor2
    fake_pool.get_connection.return_value = fake_conn

    with patch.object(mysql.connector, "connect", return_value=fake_root), \
         patch.object(pooling, "MySQLConnectionPool", return_value=fake_pool):

        init_db()

    # Root tables create
    fake_cursor.execute.assert_any_call(
        f"CREATE DATABASE IF NOT EXISTS syslog_new"
    )

    # Table creation block executed
    assert fake_cursor2.execute.called
    assert fake_conn.commit.called


def test_init_db_root_fail():
    """Root connection fails 3 times → raises exception."""
    with patch.object(mysql.connector, "connect", side_effect=mysql.connector.Error("fail")):
        with pytest.raises(mysql.connector.Error):
            init_db()


def test_init_db_table_creation_error():
    """Failure during table creation → 500 raised from get_db_connection phase."""
    fake_root = MagicMock()
    fake_root.cursor.return_value = MagicMock()

    fake_pool = MagicMock()
    fake_conn = MagicMock()

    fake_cursor2 = MagicMock()
    fake_cursor2.execute.side_effect = Exception("table boom")

    fake_conn.cursor.return_value = fake_cursor2
    fake_pool.get_connection.return_value = fake_conn

    with patch.object(mysql.connector, "connect", return_value=fake_root), \
         patch.object(pooling, "MySQLConnectionPool", return_value=fake_pool):

        with pytest.raises(Exception):
            init_db()
