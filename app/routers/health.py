# app/routers/health.py
from fastapi import APIRouter
from app.config import settings

router = APIRouter()

@router.get("/health")
def health():
    return {"status": "ok", "listening_port": settings.SYSLOG_PORT}
