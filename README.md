# Syslog Server (FastAPI)

Modular FastAPI project that listens for RFC3164 syslog messages over UDP, applies profile-based filtering,
stores internal incidents in MySQL, and forwards external profiles to configured integrations.

## Quickstart

1. Copy `.env.example` -> `.env` and fill DB credentials.
2. Install dependencies:
pip install -r requirements.txt

3. Run with uvicorn:


uvicorn app.main:app --host 0.0.0.0 --port 8000


API root: `http://localhost:8000/docs`
Syslog UDP listener will bind to port configured in `.env` (default 6666).