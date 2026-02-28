# 🐴 Twilio ↔ Gemini Live — Real-Time Voice Bridge

Sub-second latency voice calls powered by Gemini 2.0 Flash Live API + Twilio Media Streams.

```
Caller → Twilio → mulaw 8kHz → [this server] → PCM 16kHz → Gemini Live
                                     ↑                            |
                               mulaw 8kHz   ←  PCM 24kHz  ←──────┘
```

---

## ⚡ Quick Start

### 1. Install deps
```bash
pip install -r requirements.txt
```

### 2. Set your API key
```bash
cp .env.example .env
# edit .env → add your GEMINI_API_KEY
set GEMINI_API_KEY=your_key_here   # Windows
```

### 3. Run the server
```bash
python main.py
# Server starts on http://0.0.0.0:8000
```

### 4. Expose via ngrok
```bash
ngrok http 8000
# → copy the https URL, e.g. https://abc123.ngrok.io
```

### 5. Configure Twilio
1. Go to [Twilio Console](https://console.twilio.com) → Phone Numbers → your number
2. Under **Voice & Fax** → **A CALL COMES IN** set:
   - Webhook: `https://abc123.ngrok.io/voice`
   - HTTP Method: `POST`
3. Save. Call your Twilio number. Speak. Gemini answers. 🎤

---

## Architecture

| Component | Role |
|-----------|------|
| `POST /voice` | Returns TwiML that opens a Media Stream back to us |
| `WS /media-stream` | Bridges Twilio ↔ Gemini in real-time |
| `audioop` | stdlib mulaw↔PCM conversion + resampling (zero latency) |
| Gemini `Aoede` voice | Fast, natural-sounding TTS output |

## Audio Pipeline
- **Twilio → Gemini**: mulaw 8kHz → PCM 16kHz (`audioop.ulaw2lin` + `ratecv`)
- **Gemini → Twilio**: PCM 24kHz → mulaw 8kHz (`audioop.ratecv` + `lin2ulaw`)

## Customisation
- Change `SYSTEM_PROMPT` in `main.py` for a custom persona
- Change `voiceName` to: `Puck`, `Charon`, `Kore`, `Fenrir`, `Aoede`, `Leda`, `Orus`, `Zephyr`
- Change `GEMINI_MODEL` to `gemini-2.0-flash-live-001` (fastest) or `gemini-2.0-pro-live-001`
