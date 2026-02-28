"""
main.py — Application entry point.
"""

import logging
import uvicorn
from fastapi import FastAPI
import time
import platform
import sys as _sys

# Ensure `src/` is on sys.path so packages under `src` can be imported as
# top-level modules (e.g. `core`, `functions`, `routes`). This keeps
# the rest of the code unchanged and avoids rewriting many imports.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.joinpath("src")))

from src.routes.incoming     import router as incoming_router
from src.routes.media_stream import router as media_stream_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

app = FastAPI(title="Twilio ↔ Gemini Live Voice Bridge")
app.include_router(incoming_router)
app.include_router(media_stream_router)

# Health / diagnostics
START_TIME = time.time()


@app.get("/health")
async def health():
    """Simple health endpoint reporting uptime and basic system info."""
    uptime = time.time() - START_TIME
    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 2),
        "python": _sys.version.split()[0],
        "platform": platform.system(),
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=True)
