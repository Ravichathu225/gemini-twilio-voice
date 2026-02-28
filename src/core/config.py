"""
core/config.py — Central config loaded from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set.")

GEMINI_MODEL  = "gemini-2.5-flash-native-audio-preview-09-2025"
GEMINI_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
    f"?key={GEMINI_API_KEY}"
)

# Audio
TWILIO_RATE = 8000
GEMINI_IN   = 16000
GEMINI_OUT  = 24000
GEMINI_VOICE = os.getenv("DEFAULT_SYSTEM_VOICE", "Aoede")
GEMINI_SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "You are a helpful assistant.")

# VAD — tuned for letter-by-letter spelling (longer silence tolerance)
VAD_SILENCE_MS        = 100   # was 300 — callers pause between letters while spelling
VAD_PREFIX_PADDING    = 20
VAD_START_SENSITIVITY = "START_SENSITIVITY_HIGH"
VAD_END_SENSITIVITY   = "END_SENSITIVITY_LOW"   # LOW = less aggressive cutoff, avoids cutting mid-name

# Twilio
TWILIO_ACCOUNT_SID    = os.getenv("TWILIO_ACCOUNT_ID", "")
TWILIO_AUTH_TOKEN     = os.getenv("TWILIO_ACCOUNT_TOKEN", "")
TWILIO_PHONE_NUMBER   = os.getenv("TWILIO_PHONE_NUMBER", "")
TRANSFER_PHONE_NUMBER = os.getenv("TRANSFER_PHONE_NUMBER", "")

# Halo Connect (clinic DB)
HALO_BASE_URL         = os.getenv("HALO_BASE_URL", "https://api.stage.haloconnect.io")
HALO_SUBSCRIPTION_KEY = os.getenv("HALO_SUBSCRIPTION_KEY", "")
HALO_SITE_ID          = os.getenv("HALO_SITE_ID", "")

