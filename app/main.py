# app/main.py
import asyncio
import uvicorn
from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.utils.logging import configure_logging
from app.routers import profiles, integrations, profile_devices, incidents, health, cache_monitor
from app.services.udp_server import start_udp_server

configure_logging()

app = FastAPI(title="Syslog Server")

# include routers
app.include_router(health.router, prefix="", tags=["health"])
app.include_router(profiles.router, prefix="/api", tags=["profiles"])
app.include_router(integrations.router, prefix="/api", tags=["integrations"])
app.include_router(profile_devices.router, prefix="/api", tags=["profile_devices"])
app.include_router(incidents.router, prefix="/api", tags=["incidents"])
app.include_router(cache_monitor.router, prefix="/api")

udp_task: asyncio.Task | None = None

@app.on_event("startup")
async def on_startup():
    # Init DB (creates DB/tables if needed)
    init_db()
    # Start UDP server task in background (runs in same process)
    global udp_task
    udp_task = asyncio.create_task(start_udp_server())

@app.on_event("shutdown")
async def on_shutdown():
    global udp_task
    if udp_task:
        udp_task.cancel()
        try:
            await udp_task
        except asyncio.CancelledError:
            pass
        

if __name__ == "__main__":
    # If you want to run directly: `python -m app.main`
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.SYSLOG_PORT, log_level="info")
