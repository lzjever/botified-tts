# Botified TTS sidecar

This companion reads Botified `stream_text` observer frames, streams assistant
text to `botified-tts`, and plays its 48 kHz mono PCM with
`/usr/bin/aplay`. User text, provider replacement, and stdin EOF cancel the
current speech.

Requirements:

- Botified has `llm_text_preview.enabled: true`;
- `botified-tts` is reachable through its WebSocket endpoint;
- `/usr/bin/aplay` is installed and the service user can open the ALSA device;
- `/opt/botified-tts/botified-tts.env` contains exactly one unquoted
  `BOTIFIED_TTS_API_KEY=replace_with_random_hex` assignment readable by the
  service user.

Install the locked lightweight environment without the TTS service's Torch/CUDA
dependencies:

```bash
uv sync --project companions/botified --locked --no-dev
```

For companion development:

```bash
uv sync --project companions/botified --locked
uv run --project companions/botified --locked pytest -q companions/botified/tests
uv run --project companions/botified --locked ruff check companions/botified
```

The recommended long-running launch is a normal Botified task preset. These
two minimal variants select either a stored profile or Voice Design:

```yaml
task_presets:
  presets:
    botified-tts-profile:
      description: Speaks live assistant text with a stored voice profile.
      command: "/opt/botified-tts/companions/botified/.venv/bin/python /opt/botified-tts/companions/botified/sidecar.py --env-file /opt/botified-tts/botified-tts.env --tts-url ws://127.0.0.1:8000/v1/speech/stream --voice-id voice_0123456789abcdef0123456789abcdef --mode controllable --style 'calm and conversational'"
    botified-tts-design:
      description: Speaks live assistant text with Voice Design.
      command: "/opt/botified-tts/companions/botified/.venv/bin/python /opt/botified-tts/companions/botified/sidecar.py --env-file /opt/botified-tts/botified-tts.env --tts-url ws://127.0.0.1:8000/v1/speech/stream --design 'A warm, natural voice' --style 'gentle'"
  start_on_boot:
    - botified-tts-profile
```

Replace `/opt/botified-tts` with the actual absolute checkout path. Remove the
`start_on_boot` entry or select the design preset if appropriate. For TLS, pass
the complete `wss://.../v1/speech/stream` endpoint; the companion does not
rewrite URLs. Voice, mode, and style options are fixed for the process lifetime.
Presets run with no automatic timeout and with interactive stdio enabled; their
schema contains only `description` and `command`. Do not add
`interactive_stdio: false`, because the sidecar needs Botified's managed
stdin/stdout frames. It exits nonzero when observer setup, TTS streaming, or
`aplay` fails.
