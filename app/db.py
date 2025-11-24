# app/db.py
import contextlib
import time
import logging
from typing import Optional
import asyncio

import mysql.connector
from mysql.connector import pooling
from app.config import settings

logger = logging.getLogger("app.db")

_pool: Optional[pooling.MySQLConnectionPool] = None

DB_CONFIG = {
    "user": settings.DB_USER,
    "password": settings.DB_PASSWORD,
    "host": settings.DB_HOST,
    "port": settings.DB_PORT,
}

# -------------------------------------------------------------
# INIT CONNECTION POOL
# -------------------------------------------------------------
def _init_pool(db_name: str):
    global _pool
    if _pool is not None:
        return

    cfg = DB_CONFIG.copy()
    cfg["database"] = db_name
    cfg["pool_name"] = settings.DB_POOL_NAME
    cfg["pool_size"] = settings.DB_POOL_SIZE

    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            _pool = pooling.MySQLConnectionPool(**cfg)
            logger.info("DB pool initialized (size=%d)", settings.DB_POOL_SIZE)
            return
        except mysql.connector.Error as e:
            logger.warning("DB pool init failed attempt %d: %s", attempt, e)
            if attempt == attempts:
                logger.exception("Failed to init DB pool after retries")
                raise
            time.sleep(1)


# -------------------------------------------------------------
# GET CONNECTION
# -------------------------------------------------------------
@contextlib.contextmanager
def get_db_connection(db_name: Optional[str] = None):
    global _pool
    if _pool is None:
        _init_pool(db_name or settings.DB_NAME)

    cnx = None
    try:
        cnx = _pool.get_connection()
        yield cnx
    except mysql.connector.Error:
        logger.exception("DB connection error")
        raise
    finally:
        if cnx:
            try:
                cnx.close()
            except Exception:
                logger.exception("Error closing DB connection")


# -------------------------------------------------------------
# INIT DATABASE & TABLES
# -------------------------------------------------------------
def init_db():
    tmp_cfg = DB_CONFIG.copy()

    # Create DB first
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            cnx_root = mysql.connector.connect(**tmp_cfg)
            break
        except mysql.connector.Error as e:
            logger.warning("Unable to connect to DB server (attempt %d): %s", attempt, e)
            if attempt == attempts:
                logger.exception("Exceeded DB connection retries")
                raise
            time.sleep(1)

    cur = cnx_root.cursor()
    try:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {settings.DB_NAME}")
        cnx_root.commit()
    finally:
        cur.close()
        cnx_root.close()

    _init_pool(settings.DB_NAME)

    # Create tables
    with get_db_connection(settings.DB_NAME) as cnx:
        cursor = cnx.cursor()
        try:
            # --------------------------
            # syslog_profiles
            # --------------------------
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS syslog_profiles (
                    id CHAR(36) PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    type ENUM('internal','external') NOT NULL,
                    docker_name VARCHAR(100),
                    priorities JSON DEFAULT NULL,
                    facilities JSON DEFAULT NULL,
                    keywords JSON DEFAULT NULL
                ) ENGINE=InnoDB;
                """
            )

            # index on type
            cursor.execute("SHOW INDEX FROM syslog_profiles WHERE Key_name=%s", ("idx_syslog_profiles_type",))
            if not cursor.fetchone():
                cursor.execute("CREATE INDEX idx_syslog_profiles_type ON syslog_profiles(type)")

            # --------------------------
            # syslog_integration
            # --------------------------
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS syslog_integration (
                    id CHAR(36) PRIMARY KEY,
                    profile_id CHAR(36) NOT NULL,
                    destination_name VARCHAR(100),
                    ip_address VARCHAR(45),
                    port INT,
                    auth_token VARCHAR(255) NULL,
                    docker_name VARCHAR(100),
                    FOREIGN KEY (profile_id) REFERENCES syslog_profiles(id) ON DELETE CASCADE
                ) ENGINE=InnoDB;
                """
            )

            # --------------------------
            # syslog_profile_devices
            # --------------------------
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS syslog_profile_devices (
                    id CHAR(36) PRIMARY KEY,
                    profile_id CHAR(36) NOT NULL,
                    device_id VARCHAR(128) NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES syslog_profiles(id) ON DELETE CASCADE,
                    UNIQUE KEY ux_profile_device (profile_id, device_id)
                ) ENGINE=InnoDB;
                """
            )

            # --------------------------
            # syslog_incidents
            # --------------------------
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS syslog_incidents (
                    id CHAR(36) PRIMARY KEY,
                    device_id VARCHAR(128) NOT NULL,
                    profile_id CHAR(36),
                    priority_code INT,
                    facility_code INT,
                    message TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (profile_id) REFERENCES syslog_profiles(id) ON DELETE SET NULL
                ) ENGINE=InnoDB;
                """
            )

            # --------------------------------------------------------------
            # REQUIRED INDEXES (MySQL-safe)
            # --------------------------------------------------------------

            # index: incidents.device_id
            cursor.execute(
                "SHOW INDEX FROM syslog_incidents WHERE Key_name=%s",
                ("idx_incidents_device_id",)
            )
            if not cursor.fetchone():
                cursor.execute("CREATE INDEX idx_incidents_device_id ON syslog_incidents(device_id)")

            # index: incidents.profile_id
            cursor.execute(
                "SHOW INDEX FROM syslog_incidents WHERE Key_name=%s",
                ("idx_incidents_profile_id",)
            )
            if not cursor.fetchone():
                cursor.execute("CREATE INDEX idx_incidents_profile_id ON syslog_incidents(profile_id)")

            # index: incidents.timestamp
            cursor.execute(
                "SHOW INDEX FROM syslog_incidents WHERE Key_name=%s",
                ("idx_incidents_timestamp",)
            )
            if not cursor.fetchone():
                cursor.execute("CREATE INDEX idx_incidents_timestamp ON syslog_incidents(timestamp)")

            cnx.commit()
            logger.info("DB tables initialized successfully.")

        finally:
            cursor.close()


# -------------------------------------------------------------
# CLEANUP — BATCH DELETE OLD INCIDENTS
# -------------------------------------------------------------
def cleanup_old_incidents():
    """
    Delete syslog_incidents older than 30 days in batches of 20k.
    """
    try:
        batch_size = 20000
        total_deleted = 0

        with get_db_connection() as cnx:
            cursor = cnx.cursor()

            while True:
                cursor.execute(
                    f"""
                    DELETE FROM syslog_incidents
                    WHERE timestamp < NOW() - INTERVAL 30 DAY
                    LIMIT {batch_size}
                    """
                )
                deleted = cursor.rowcount
                cnx.commit()

                total_deleted += deleted
                if deleted < batch_size:
                    break

            cursor.close()

        logger.info(f"[CLEANUP] Deleted {total_deleted} syslog_incidents (older than 30 days)")

    except Exception as e:
        logger.error(f"[CLEANUP] Error cleaning old incidents: {e}")


# -------------------------------------------------------------
# DAILY CLEANUP SCHEDULER
# -------------------------------------------------------------
async def start_incident_cleanup_scheduler():
    await asyncio.sleep(5)
    logger.info("Starting daily syslog_incidents cleanup scheduler...")

    while True:
        cleanup_old_incidents()
        await asyncio.sleep(24 * 60 * 60)
