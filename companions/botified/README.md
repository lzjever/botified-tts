# Botified TTS sidecar

This companion reads Botified `stream_text` observer frames, streams assistant
text to `botified-tts`, and plays its 48 kHz mono PCM with
`/usr/bin/aplay`. User text, provider replacement, and stdin EOF cancel the
current speech.

Requirements:

- Botified has `llm_text_preview.enabled: true`;
- `botified-tts` is reachable through its WebSocket endpoint;
- `/usr/bin/aplay` is installed and the service user can open the ALSA device;
- the TTS bearer token is stored in a readable file.

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

The recommended long-running launch is a normal Botified task preset:

```yaml
task_presets:
  presets:
    botified-tts-sidecar:
      description: Speaks live assistant text through botified-tts.
      command: "/opt/botified-tts/companions/botified/.venv/bin/python /opt/botified-tts/companions/botified/sidecar.py --tts-url ws://127.0.0.1:8000/v1/speech/stream --api-key-file /run/secrets/botified-tts-api-key"
  start_on_boot:
    - botified-tts-sidecar
```

Replace `/opt/botified-tts` with the actual absolute checkout path. Remove the
`start_on_boot` entry if the agent should start the preset explicitly with
`task_preset_start`. Presets run with no automatic timeout and with interactive
stdio enabled; their schema contains only `description` and `command`. Do not
add `interactive_stdio: false`, because the sidecar needs Botified's managed
stdin/stdout frames. It exits nonzero when observer setup, TTS streaming, or
`aplay` fails.
