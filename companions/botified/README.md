# Botified TTS companion

This optional companion reads Botified `stream_text` observer frames, streams
assistant text to `botified-tts`, and plays 48 kHz mono PCM through
`/usr/bin/aplay` on the Botified host. User text, provider replacement, task
cancellation, and stdin EOF stop the current speech.

It is for local loudspeaker playback only. A normal TTS service deployment does
not need it, and installing the companion does not start it.

## Requirements

- Python 3.12 or newer;
- Botified `llm_text_preview.enabled: true`;
- a reachable `botified-tts` HTTP(S) service base URL;
- `/usr/bin/aplay` and access to the host ALSA device;
- `BOTIFIED_TTS_URL` and `BOTIFIED_TTS_API_KEY` in the companion process
  environment; the key uses the shared `[A-Za-z0-9._~-]+` format.

When a Botified task preset launches the companion, put those two variables in
the existing `<resolved-agents-dir>/env.d/botified-tts.env`. Botified loads
`env.d` into each new preset process. The companion does not locate or parse
that file itself. Restart an already running preset after changing the values.
Adding or changing the preset YAML itself requires restarting Botified Core
through its existing external supervisor; the companion does not add another
restart mechanism.

`BOTIFIED_TTS_URL` is the service base URL, for example
`http://tts-host:8000` or `https://tts.example`. The companion derives the
single `/v1/speech/stream` WebSocket endpoint internally.

## Install

Install the locked lightweight environment without the TTS service's
Torch/CUDA dependencies:

```bash
uv sync --project companions/botified --locked --no-dev
```

The installed command is:

```bash
companions/botified/.venv/bin/botified-tts-companion --help
```

For companion development:

```bash
uv sync --project companions/botified --locked
uv run --project companions/botified --locked pytest -q companions/botified/tests
uv run --project companions/botified --locked ruff check companions/botified
```

## Botified task presets

Register the presets without starting either one automatically:

```yaml
task_presets:
  presets:
    botified-tts-profile:
      description: Speaks live assistant text with a stored voice profile.
      command: "/opt/botified-tts/companions/botified/.venv/bin/botified-tts-companion --voice-id voice_0123456789abcdef0123456789abcdef --mode controllable --style 'calm and conversational'"
    botified-tts-design:
      description: Speaks live assistant text with Voice Design.
      command: "/opt/botified-tts/companions/botified/.venv/bin/botified-tts-companion --design 'A warm, natural voice' --style 'gentle'"
  start_on_boot: []
```

Replace `/opt/botified-tts` with the actual absolute installation path. Start
one preset only when local playback is wanted. A deployment that explicitly
needs playback after every Core restart may add that preset id to
`start_on_boot`.

Do not put the URL, API key, or an env-file argument in the preset command.
Presets must retain their default interactive stdio because the companion
receives Botified observer frames there. Voice, mode, and style are fixed for
the process lifetime. The command exits nonzero when observer setup, TTS
streaming, or `aplay` fails.
