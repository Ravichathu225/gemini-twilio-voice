"""
core/transcription.py -- TranscriptionMessage model and per-call collector.

Gemini Live emits two transcription keys inside serverContent:
  serverContent.inputTranscription.text   -> role="user"      (caller speech)
  serverContent.outputTranscription.text  -> role="assistant" (bot speech)

Both are enabled in setup via:
  inputAudioTranscription:  {}
  outputAudioTranscription: {}

TranscriptionMessage fields:
  role      : "user" | "assistant"
  content   : str                   -- transcribed text
  timestamp : str | None            -- ISO 8601 UTC, auto-set on creation
  user_id   : str | None            -- call_sid / caller id (user messages only)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

log = logging.getLogger(__name__)

# Folder where transcript JSON files are written (created automatically)
TRANSCRIPTS_DIR = Path(__file__).parent.parent / "transcripts"


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class TranscriptionMessage:
    role:      Literal["user", "assistant"]
    content:   str
    timestamp: Optional[str] = field(default=None)
    user_id:   Optional[str] = field(default=None)   # only set for role="user"

    def to_dict(self) -> dict:
        return {
            "role":      self.role,
            "content":   self.content,
            "timestamp": self.timestamp,
            "user_id":   self.user_id,
        }

    def __str__(self) -> str:
        uid = f" [{self.user_id}]" if self.user_id else ""
        ts  = f" @ {self.timestamp}" if self.timestamp else ""
        return f"[{self.role.upper()}{uid}{ts}] {self.content}"



# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class TranscriptCollector:
    """Accumulates TranscriptionMessages for one call session."""

    def __init__(self, user_id: Optional[str] = None):
        self.user_id: Optional[str] = user_id
        self._messages: list[TranscriptionMessage] = []

    def add(
        self,
        role: Literal["user", "assistant"],
        content: str,
    ) -> Optional[TranscriptionMessage]:
        """Build and store a TranscriptionMessage. Returns None for blank text."""
        content = (content or "").strip()
        if not content:
            return None
        msg = TranscriptionMessage(
            role=role,
            content=content,
            timestamp=_utc_now(),
            user_id=self.user_id if role == "user" else None,
        )
        self._messages.append(msg)
        return msg

    @property
    def messages(self) -> list[TranscriptionMessage]:
        return list(self._messages)

    def to_dict_list(self) -> list[dict]:
        return [m.to_dict() for m in self._messages]

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return f"<TranscriptCollector user_id={self.user_id!r} messages={len(self)}>"

    def save(self, call_sid: Optional[str] = None, stream_sid: Optional[str] = None) -> Optional[Path]:
        """
        Serialize all messages to JSON and write to transcripts/<filename>.json.

        File name format:
            transcript_<call_sid or stream_sid>_<YYYYMMDD_HHMMSS>.json

        JSON structure:
            {
              "call_sid":   "CA...",
              "stream_sid": "MZ...",
              "created_at": "2026-02-21T10:00:00+00:00",
              "messages": [
                {
                  "role":      "user" | "assistant",
                  "content":   "...",
                  "timestamp": "2026-02-21T10:00:01+00:00",
                  "user_id":   "CA..." | null
                },
                ...
              ]
            }

        Returns the Path of the saved file, or None if nothing to save.
        """
        if not self._messages:
            log.warning("TranscriptCollector.save() called with no messages — skipping")
            return None

        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

        sid   = call_sid or stream_sid or "unknown"
        ts    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"transcript_{sid}_{ts}.json"
        fpath = TRANSCRIPTS_DIR / fname

        payload = {
            "call_sid":   call_sid,
            "stream_sid": stream_sid,
            "created_at": _utc_now(),
            "messages":   self.to_dict_list(),
        }

        try:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            log.info(f"Transcript saved -> {fpath} ({len(self._messages)} messages)")
            return fpath
        except Exception as e:
            log.error(f"Failed to save transcript: {e}")
            return None


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
