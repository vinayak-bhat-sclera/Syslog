# app/db.py
import contextlib
import time
import logging
from typing import Optional

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


def init_db():
    tmp_cfg = DB_CONFIG.copy()

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

    with get_db_connection(settings.DB_NAME) as cnx:
        cursor = cnx.cursor()
        try:
            # --------------------------
            # syslog_profiles (network → docker_name)
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

            cursor.execute("SHOW INDEX FROM syslog_profiles WHERE Key_name = %s", ("idx_syslog_profiles_type",))
            if not cursor.fetchall():
                cursor.execute("CREATE INDEX idx_syslog_profiles_type ON syslog_profiles(type)")

            # --------------------------
            # syslog_integration (network → docker_name)
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
            # syslog_incidents (unchanged)
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

            cnx.commit()
            logger.info("DB tables initialized successfully.")
        finally:
            cursor.close()

# -------------------------------------------------------------
# NEW — CLEANUP FUNCTION
# -------------------------------------------------------------
def cleanup_old_incidents():
    """
    Delete syslog incidents older than 30 days.
    Runs once a day from background scheduler.
    """
    try:
        with get_db_connection() as cnx:
            cursor = cnx.cursor()

            cursor.execute(
                """
                DELETE FROM syslog_incidents
                WHERE timestamp < NOW() - INTERVAL 30 DAY
                """
            )
            deleted = cursor.rowcount
            cnx.commit()
            cursor.close()

        logger.info(f"[CLEANUP] Deleted {deleted} old syslog_incidents rows")

    except Exception as e:
        logger.error(f"[CLEANUP] Error cleaning old incidents: {e}")


# -------------------------------------------------------------
# NEW — BACKGROUND SCHEDULER LOOP
# -------------------------------------------------------------
async def start_incident_cleanup_scheduler():
    """
    Runs cleanup once every 24 hours 
    """
    await asyncio.sleep(5)  # Small delay to let app boot fully
    logger.info("Starting daily syslog_incidents cleanup scheduler...")

    while True:
        cleanup_old_incidents()
        await asyncio.sleep(24 * 60 * 60)  # wait 24h