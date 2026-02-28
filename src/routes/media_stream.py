"""
routes/media_stream.py -- Twilio <-> Gemini Live bridge.

Transcription:
  - inputAudioTranscription + outputAudioTranscription enabled in setup
  - serverContent.inputTranscription.text  -> collector.add("user", ...)
  - serverContent.outputTranscription.text -> collector.add("assistant", ...)
  - Every message logged: [USER] / [ASSISTANT]
  - Full transcript printed on call end
"""

import asyncio
import base64
import json
import logging
import time
from typing import Optional

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.config import (
    GEMINI_WS_URL, GEMINI_MODEL, GEMINI_IN,
    SYSTEM_PROMPT, GEMINI_VOICE,
    VAD_SILENCE_MS, VAD_PREFIX_PADDING,
    VAD_START_SENSITIVITY, VAD_END_SENSITIVITY,
)
from core.audio import mulaw_to_pcm16, pcm16_to_mulaw, reset_resample_state
from core.transcription import TranscriptCollector, _utc_now
from functions.db_functions import TOOL_DECLARATIONS, dispatch_function_call

log = logging.getLogger(__name__)
router = APIRouter()


def _build_setup_msg() -> dict:
    return {
        "setup": {
            "model": f"models/{GEMINI_MODEL}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}
                },
                "thinkingConfig": {"thinkingBudget": 0},
                "temperature": 0.7,   # lowered from 0.7 for consistent, predictable behaviour
            },
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            # Context window compression REMOVED — it was causing the bot to forget
            # conversation history mid-call and re-introduce itself ("Hi, I'm Rida").
            # A typical clinic call stays well within token limits without compression.
            "realtimeInputConfig": {
                "automaticActivityDetection": {
                    "startOfSpeechSensitivity": VAD_START_SENSITIVITY,
                    "endOfSpeechSensitivity":   VAD_END_SENSITIVITY,
                    "prefixPaddingMs":           VAD_PREFIX_PADDING,
                    "silenceDurationMs":         VAD_SILENCE_MS,
                }
            },
            # ── Transcription flags ────────────────────────────────────────
            "inputAudioTranscription":  {},   # caller speech  -> role="user"
            "outputAudioTranscription": {},   # bot speech     -> role="assistant"
            "tools": [{"functionDeclarations": TOOL_DECLARATIONS}],
        }
    }


@router.websocket("/media-stream")
async def media_stream(twilio_ws: WebSocket):
    await twilio_ws.accept()
    reset_resample_state()
    log.info("Twilio WS connected")

    stream_sid: Optional[str] = None
    call_sid:   Optional[str] = None
    sid_ready   = asyncio.Event()
    audio_queue: asyncio.Queue = asyncio.Queue()
    done = asyncio.Event()

    async with websockets.connect(
        GEMINI_WS_URL,
        ping_interval=20,
        max_size=2 ** 22,
        compression=None,
    ) as gem_ws:
        log.info("Gemini WS opened")

        await gem_ws.send(json.dumps(_build_setup_msg()))
        setup_resp = json.loads(await gem_ws.recv())
        log.info(f"Gemini ready -- {list(setup_resp.keys())}")

        # Wait for the Twilio audio stream to fully establish before greeting.
        # A 2-second delay prevents the greeting from being clipped or lost.
        # Trigger greeting — send a clear one-time prompt so the bot greets the caller.
        # Using a unique phrase that the system prompt does NOT echo.
        await gem_ws.send(json.dumps({
            "clientContent": {
                "turns": [{"role": "user", "parts": [{"text": "__BEGIN_CALL__"}]}],
                "turnComplete": True,
            }
        }))
        log.info("Greeting triggered")

        # One collector per call -- user_id filled once call_sid is known
        transcript = TranscriptCollector()

        # Streaming buffers — simple objects avoid all nonlocal/closure scoping issues.
        # Gemini streams transcription word-by-word; we accumulate and flush on turnComplete.
        class _Buf:
            def __init__(self):
                self.chunks: list[str] = []
                self.ts: Optional[str] = None   # ISO timestamp of first chunk this turn

            def append(self, text: str) -> None:
                if not self.ts:
                    self.ts = _utc_now()
                self.chunks.append(text.strip())

            def flush(self, role: str) -> Optional[str]:
                """Return joined text and reset. Returns None if empty."""
                text = " ".join(self.chunks).strip()
                ts   = self.ts
                self.chunks.clear()
                self.ts = None
                return (text, ts) if text else None

        user_buf = _Buf()
        asst_buf = _Buf()

        def _flush_buffers() -> None:
            from core.transcription import TranscriptionMessage
            result = asst_buf.flush("assistant")
            if result:
                text, ts = result
                msg = TranscriptionMessage(role="assistant", content=text, timestamp=ts, user_id=None)
                transcript._messages.append(msg)
                log.info(str(msg))
            result = user_buf.flush("user")
            if result:
                text, ts = result
                msg = TranscriptionMessage(role="user", content=text, timestamp=ts, user_id=transcript.user_id)
                transcript._messages.append(msg)
                log.info(str(msg))

        # ── Sender: raw PCM queue -> convert -> Twilio ────────────────────────
        async def send_audio_to_twilio():
            await sid_ready.wait()
            log.info(f"Audio sender ready sid={stream_sid}")
            while not done.is_set():
                try:
                    item = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                try:
                    mulaw = await pcm16_to_mulaw(item)
                    await twilio_ws.send_text(json.dumps({
                        "event":     "media",
                        "streamSid": stream_sid,
                        "media":     {"payload": base64.b64encode(mulaw).decode()},
                    }))
                except Exception as e:
                    log.warning(f"sender error: {e}")
                    done.set()
                    break

        # ── Gemini receiver: audio + transcription + tool calls ───────────────
        async def gemini_to_twilio():
            try:
                async for raw in gem_ws:
                    if done.is_set():
                        break
                    msg = json.loads(raw)

                    # Detect Gemini error frames (e.g. quota, model errors)
                    if "error" in msg:
                        err = msg["error"]
                        log.error(f"Gemini error frame: code={err.get('code')} msg={err.get('message')}")
                        # Don't silently drop — let the call continue but log clearly
                        continue

                    # Tool call -- non-blocking background task
                    tool_call = msg.get("toolCall")
                    if tool_call:
                        asyncio.create_task(_handle_tool_call(tool_call, gem_ws))
                        continue

                    sc = msg.get("serverContent", {})

                    # ── Caller speech transcription — buffer chunks ───────────
                    input_tx = sc.get("inputTranscription", {})
                    if input_tx.get("text"):
                        user_buf.append(input_tx["text"])

                    # ── Bot speech transcription — buffer chunks ──────────────
                    output_tx = sc.get("outputTranscription", {})
                    if output_tx.get("text"):
                        asst_buf.append(output_tx["text"])

                    # ── turnComplete -> flush both buffers into one message ────
                    if sc.get("turnComplete"):
                        _flush_buffers()

                    # ── Audio chunks -> queue raw PCM ─────────────────────────
                    mt = sc.get("modelTurn")
                    if mt:
                        for part in mt.get("parts", []):
                            inline = part.get("inlineData", {})
                            if inline.get("mimeType", "").startswith("audio/pcm"):
                                pcm = base64.b64decode(inline["data"])
                                await audio_queue.put(pcm)

            except Exception as e:
                log.warning(f"gemini_to_twilio error: {e}")
            finally:
                await audio_queue.put(None)
                log.info("gemini_to_twilio exited")

        # ── Tool call handler (independent task, non-blocking) ────────────────
        async def _handle_tool_call(tool_call: dict, ws):
            t0 = time.monotonic()
            responses = []
            for fc in tool_call.get("functionCalls", []):
                fn_name = fc.get("name", "")
                fn_id   = fc.get("id", "")
                fn_args = fc.get("args", {})
                log.info(f"-> tool: {fn_name}({fn_args})")
                result = await dispatch_function_call(fn_name, fn_args, call_sid or "")
                log.info(f"<- tool: {fn_name} {(time.monotonic()-t0)*1000:.0f}ms -> {result}")
                responses.append({"id": fn_id, "name": fn_name, "response": result})
            try:
                await ws.send(json.dumps({"toolResponse": {"functionResponses": responses}}))
            except Exception as e:
                log.warning(f"toolResponse send error: {e}")

        # ── Twilio -> Gemini ──────────────────────────────────────────────────
        async def twilio_to_gemini():
            nonlocal stream_sid, call_sid
            try:
                async for raw in twilio_ws.iter_text():
                    data  = json.loads(raw)
                    event = data.get("event")

                    if event == "start":
                        sd = data["start"]
                        stream_sid = sd["streamSid"]
                        call_sid   = sd.get("callSid") or sd.get("customParameters", {}).get("callSid")
                        transcript.user_id = call_sid  # attach caller id to all user messages
                        log.info(f"Stream started sid={stream_sid} call={call_sid}")
                        sid_ready.set()

                    elif event == "media":
                        mulaw = base64.b64decode(data["media"]["payload"])
                        pcm   = await mulaw_to_pcm16(mulaw)
                        await gem_ws.send(json.dumps({
                            "realtimeInput": {
                                "audio": {
                                    "data":     base64.b64encode(pcm).decode(),
                                    "mimeType": f"audio/pcm;rate={GEMINI_IN}",
                                }
                            }
                        }))

                    elif event == "stop":
                        log.info(f"Stream stopped sid={stream_sid}")
                        break

            except WebSocketDisconnect:
                log.info("Twilio WS disconnected")
            except Exception as e:
                log.warning(f"twilio_to_gemini error: {e}")

        # ── Run ───────────────────────────────────────────────────────────────
        gem_task    = asyncio.create_task(gemini_to_twilio())
        sender_task = asyncio.create_task(send_audio_to_twilio())

        try:
            await twilio_to_gemini()
        finally:
            done.set()
            sid_ready.set()
            await audio_queue.put(None)

            for task in (gem_task, sender_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            try:
                await gem_ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                await gem_ws.close()
            except Exception:
                pass

            # ── Flush any remaining buffered transcription chunks ─────────────
            _flush_buffers()

            # ── Save transcript to JSON file ──────────────────────────────
            if transcript.messages:
                log.info(f"=== TRANSCRIPT [{call_sid}] ({len(transcript)} messages) ===")
                for m in transcript.messages:
                    log.info(str(m))
                log.info("=== END TRANSCRIPT ===")
                transcript.save(call_sid=call_sid, stream_sid=stream_sid)

            reset_resample_state()
            log.info(f"Cleanup complete sid={stream_sid}")
