---
name: voxcpm-tts
description: Generate WAV speech and manage trusted Botified TTS voice profiles through the bundled HTTP helper. Use when an agent needs to check Botified TTS health, create, list, or delete a voice profile, or synthesize normal, designed, controllable-clone, or faithful-clone speech to an explicit local WAV path.
---

# VoxCPM TTS

Resolve `scripts/botified-tts` relative to this `SKILL.md` and use that bundled
helper. Do not reimplement its HTTP calls.

Configure Botified `skills.explicit` to point directly to this repository's
`skills/voxcpm-tts/SKILL.md`. Do not copy or symlink the Skill, and do not
create a second discovery path.

Set the HTTP service URL and use the same private `botified-tts.env` format as
the Docker service:

```bash
export BOTIFIED_TTS_URL=http://127.0.0.1:8000
TTS=<skill-directory>/scripts/botified-tts
ENV_FILE=/opt/botified-tts/botified-tts.env

"${TTS}" --env-file "${ENV_FILE}" voice-list
```

The env file may contain the service's other variables, but must contain
exactly one literal API key assignment:

```text
BOTIFIED_TTS_API_KEY=replace_with_random_hex
BOTIFIED_TTS_MODEL_SOURCE=modelscope
```

The unquoted key must match `[A-Za-z0-9._~-]+`. The helper does not source the
file or evaluate quotes, interpolation, command substitution, or shell syntax.

Check health when service status is uncertain:

```bash
"${TTS}" health
```

Manage voice profiles:

```bash
"${TTS}" --env-file "${ENV_FILE}" voice-create \
  --name assistant \
  --file reference.wav \
  --prompt-text 'The exact words spoken in the reference.'
"${TTS}" --env-file "${ENV_FILE}" voice-list
"${TTS}" --env-file "${ENV_FILE}" voice-delete \
  --id voice_0123456789abcdef0123456789abcdef
```

Provide `--prompt-text` only when it exactly matches the reference audio. A
profile requires that transcript before it can use faithful mode.

Pass only final, already speakable plain text to `--text`. Do not pass Markdown
or SSML; the service does not parse either. Always provide a new explicit WAV
output path when synthesizing:

```bash
# Normal voice
"${TTS}" --env-file "${ENV_FILE}" speak \
  --text 'Hello.' --output hello.wav

# Voice Design
"${TTS}" --env-file "${ENV_FILE}" speak \
  --text 'Hello.' \
  --output designed.wav \
  --design 'A warm, natural young voice'

# Controllable clone; this is the default profile mode
"${TTS}" --env-file "${ENV_FILE}" speak \
  --text 'Hello.' \
  --output clone.wav \
  --voice-id voice_0123456789abcdef0123456789abcdef \
  --style 'calm and conversational'

# Faithful clone; style is not accepted
"${TTS}" --env-file "${ENV_FILE}" speak \
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
