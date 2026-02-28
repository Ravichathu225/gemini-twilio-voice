"""
routes/incoming.py — HTTP route: handles inbound Twilio voice calls.

POST /voice
  Twilio hits this when a call arrives. We return TwiML instructing Twilio
  to open a bidirectional Media Stream back to our WebSocket endpoint.
"""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import Response

log = logging.getLogger(__name__)
router = APIRouter()


@router.api_route("/incoming-call", methods=["GET", "POST"])
async def incoming_call(request: Request):
    """
    Respond to an incoming Twilio call with TwiML that:
      1. Greets the caller with a quick message.
      2. Opens a <Stream> back to our /media-stream WebSocket.
    """
    host   = request.headers.get("host", "your-server.ngrok.io")
    ws_url = f"wss://{host}/media-stream"

    # CallSid arrives in query string for GET, form body for POST
    params  = dict(request.query_params)
    call_sid = params.get("CallSid", "unknown")

    log.info(f"Incoming call {call_sid} → opening media stream to {ws_url}")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}" />
  </Connect>
</Response>"""

    return Response(content=twiml, media_type="application/xml")
