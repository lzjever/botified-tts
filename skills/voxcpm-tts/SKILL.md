---
name: voxcpm-tts
description: Generate WAV speech and manage trusted Botified TTS voice profiles through the bundled HTTP helper. Use when an agent needs to check Botified TTS health, create, list, or delete a voice profile, or synthesize normal, designed, controllable-clone, or faithful-clone speech to an explicit local WAV path.
---

# VoxCPM TTS

Resolve `scripts/botified-tts` relative to this `SKILL.md` and use that bundled
helper. Do not reimplement its HTTP calls.

Set the service URL. For authenticated commands, pass the raw API key file as
the global option before the command. The file must contain one non-empty ASCII
line with no leading or trailing whitespace:

```bash
export BOTIFIED_TTS_URL=http://127.0.0.1:8000
TTS=<skill-directory>/scripts/botified-tts

"${TTS}" --api-key-file /secure/path/botified-tts-api-key voice-list
```

Check health when service status is uncertain:

```bash
"${TTS}" health
```

Manage voice profiles:

```bash
"${TTS}" --api-key-file /secure/path/botified-tts-api-key voice-create \
  --name assistant \
  --file reference.wav \
  --prompt-text 'The exact words spoken in the reference.'
"${TTS}" --api-key-file /secure/path/botified-tts-api-key voice-list
"${TTS}" --api-key-file /secure/path/botified-tts-api-key voice-delete \
  --id voice_0123456789abcdef0123456789abcdef
```

Provide `--prompt-text` only when it exactly matches the reference audio. A
profile requires that transcript before it can use faithful mode.

Pass only final, already speakable plain text to `--text`. Do not pass Markdown
or SSML; the service does not parse either. Always provide a new explicit WAV
output path when synthesizing:

```bash
# Normal voice
"${TTS}" --api-key-file /secure/path/botified-tts-api-key speak \
  --text 'Hello.' --output hello.wav

# Voice Design
"${TTS}" --api-key-file /secure/path/botified-tts-api-key speak \
  --text 'Hello.' \
  --output designed.wav \
  --design 'A warm, natural young voice'

# Controllable clone; this is the default profile mode
"${TTS}" --api-key-file /secure/path/botified-tts-api-key speak \
  --text 'Hello.' \
  --output clone.wav \
  --voice-id voice_0123456789abcdef0123456789abcdef \
  --style 'calm and conversational'

# Faithful clone; style is not accepted
"${TTS}" --api-key-file /secure/path/botified-tts-api-key speak \
  --text 'Hello.' \
  --output faithful.wav \
  --voice-id voice_0123456789abcdef0123456789abcdef \
  --mode faithful
```

Report the returned voice ID after creation and the output path after synthesis.
The helper refuses to overwrite an existing output.

Use this Skill only to generate files and manage profiles. Do not use it for
token streaming or realtime playback; this repository's `companions/botified`
owns that integration. Do not add an adapter to the Botified repository.
