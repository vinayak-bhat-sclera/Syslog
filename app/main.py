# app/main.py
import asyncio
import uvicorn
from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.utils.logging import configure_logging
from app.routers import profiles, integrations, profile_devices, incidents, health, cache_monitor
from app.services.udp_server import start_udp_server
from app.db import start_incident_cleanup_scheduler

# NEW: import cache background task manager
from app.services.device_cache import start_background_tasks, stop_background_tasks

configure_logging()

app = FastAPI(title="Syslog Server")

# ROUTERS (NO /api PREFIX)
app.include_router(health.router, prefix="", tags=["health"])
app.include_router(profiles.router, prefix="", tags=["profiles"])
app.include_router(integrations.router, prefix="", tags=["integrations"])
app.include_router(profile_devices.router, prefix="", tags=["profile_devices"])
app.include_router(incidents.router, prefix="", tags=["incidents"])
app.include_router(cache_monitor.router, prefix="")

udp_task: asyncio.Task | None = None
device_cache_task: asyncio.Task | None = None


@app.on_event("startup")
async def on_startup():
    # Initialize DB
    init_db()

    # Start UDP Syslog Server
    global udp_task
    udp_task = asyncio.create_task(start_udp_server())

    # Start background device-cache refresh + snapshot task
    global device_cache_task
    device_cache_task = start_background_tasks()

    # ✅ Start daily cleanup scheduler (delete syslog_incidents older than 30 days)
    asyncio.create_task(start_incident_cleanup_scheduler())
    


@app.on_event("shutdown")
async def on_shutdown():
    # Shutdown UDP Server
    global udp_task
    if udp_task:
        udp_task.cancel()
        try:
            await udp_task
        except asyncio.CancelledError:
            pass

    # Shutdown device cache background task
    global device_cache_task
    stop_background_tasks(device_cache_task)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.SYSLOG_PORT,
        log_level="info"
    )
