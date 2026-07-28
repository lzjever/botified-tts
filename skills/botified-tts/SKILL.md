---
name: botified-tts
description: Generate WAV speech and manage trusted Botified TTS voice profiles through the bundled HTTP helper. Use when an agent needs to check Botified TTS health, create, list, or delete a voice profile, or synthesize normal, designed, controllable-clone, or faithful-clone speech to an explicit local WAV path.
---

# Botified TTS

Use the bundled `scripts/botified-tts` helper. Do not reimplement its HTTP calls.

Set:

```bash
export BOTIFIED_TTS_URL=http://127.0.0.1:8000
export BOTIFIED_TTS_API_KEY=...
```

Check health when service status is uncertain:

```bash
scripts/botified-tts health
```

Manage voice profiles:

```bash
scripts/botified-tts voice-create \
  --name assistant \
  --file reference.wav \
  --prompt-text 'The exact words spoken in the reference.'
scripts/botified-tts voice-list
scripts/botified-tts voice-delete --id voice_0123456789abcdef0123456789abcdef
```

Provide `--prompt-text` only when it exactly matches the reference audio. A
profile requires that transcript before it can use faithful mode.

Always provide a new explicit WAV output path when synthesizing:

```bash
# Normal voice
scripts/botified-tts speak --text 'Hello.' --output hello.wav

# Voice Design
scripts/botified-tts speak \
  --text 'Hello.' \
  --output designed.wav \
  --design 'A warm, natural young voice'

# Controllable clone; this is the default profile mode
scripts/botified-tts speak \
  --text 'Hello.' \
  --output clone.wav \
  --voice-id voice_0123456789abcdef0123456789abcdef \
  --style 'calm and conversational'

# Faithful clone; style is not accepted
scripts/botified-tts speak \
  --text 'Hello.' \
  --output faithful.wav \
  --voice-id voice_0123456789abcdef0123456789abcdef \
  --mode faithful
```

Report the returned voice ID after creation and the output path after synthesis.
The helper refuses to overwrite an existing output.

Use this Skill only to generate files and manage profiles. Do not use it for
token streaming or realtime playback; Botified runtime owns the WebSocket
stream.
