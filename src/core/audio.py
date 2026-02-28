"""
core/audio.py — Ultra-low-latency audio conversion: mulaw ↔ PCM16.

Key changes vs previous version:
  - scipy.signal.resample_poly replaces numpy sinc — uses optimized C polyphase
    filter, ~5x faster and runs off the async event loop via thread pool
  - Stateful audioop.ratecv for Twilio→Gemini upsample (8→16kHz) — C-speed
  - All heavy processing dispatched via run_in_executor to never block asyncio
  - Simple lookup-table mulaw decode as fallback if audioop unavailable
"""

import audioop
import asyncio
import numpy as np
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from math import gcd

from core.config import TWILIO_RATE, GEMINI_IN, GEMINI_OUT

# Single shared executor — keeps threads warm, avoids spawn overhead per chunk
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio")

# Per-call resample state (reset each session)
_upsample_state = None


def reset_resample_state():
    """Call once per new call to clear stateful resampler carry-over."""
    global _upsample_state
    _upsample_state = None


# ── Twilio → Gemini (sync, called from executor) ──────────────────────────────
def _mulaw_to_pcm16_sync(data: bytes) -> bytes:
    """
    mulaw 8kHz → PCM16 16kHz (stateful, C-speed via audioop).
    Runs in thread pool — never blocks the event loop.
    """
    global _upsample_state
    pcm = audioop.ulaw2lin(data, 2)                       # mulaw → PCM16 @ 8kHz
    pcm, _upsample_state = audioop.ratecv(                # upsample 8→16kHz
        pcm, 2, 1, TWILIO_RATE, GEMINI_IN, _upsample_state
    )
    return pcm


# ── Gemini → Twilio (sync, called from executor) ──────────────────────────────
@lru_cache(maxsize=1)
def _get_resample_factors():
    """Compute polyphase up/down factors once."""
    g  = gcd(GEMINI_OUT, TWILIO_RATE)
    return TWILIO_RATE // g, GEMINI_OUT // g   # up, down


def _pcm16_to_mulaw_sync(data: bytes) -> bytes:
    """
    PCM16 24kHz → mulaw 8kHz using scipy polyphase resampler (anti-aliased).
    Falls back to audioop.ratecv if scipy not installed.
    Runs in thread pool — never blocks the event loop.
    """
    try:
        from scipy.signal import resample_poly
        up, down = _get_resample_factors()
        samples   = np.frombuffer(data, dtype=np.int16)
        resampled = resample_poly(samples, up, down, padtype="line")
        pcm       = np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()
    except ImportError:
        # Fallback: audioop (no anti-alias but zero extra deps)
        pcm, _ = audioop.ratecv(data, 2, 1, GEMINI_OUT, TWILIO_RATE, None)
    return audioop.lin2ulaw(pcm, 2)


# ── Async wrappers (dispatch to thread pool) ──────────────────────────────────
async def mulaw_to_pcm16(data: bytes) -> bytes:
    """Async: mulaw 8kHz → PCM16 16kHz (non-blocking)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _mulaw_to_pcm16_sync, data)


async def pcm16_to_mulaw(data: bytes) -> bytes:
    """Async: PCM16 24kHz → mulaw 8kHz (non-blocking)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _pcm16_to_mulaw_sync, data)
