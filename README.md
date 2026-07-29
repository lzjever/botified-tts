# Botified TTS

Botified TTS is a standalone VoxCPM2 speech service for Botified. It provides
HTTP WAV and Ogg/Opus synthesis, bidirectional WebSocket streaming, Voice
Design, controllable and faithful voice cloning, style instructions, and
native VoxCPM2 text tags.

The service requires Linux x86_64, Docker, an NVIDIA GPU with a CUDA-compatible
host driver, and NVIDIA Container Toolkit configured for Docker. There is no
CPU, ROCm, Apple Silicon, Windows, or multi-GPU fallback.

## Run the service

Service users do not need to check out this repository. Create the only private
configuration file:

```bash
umask 077
{
  printf 'BOTIFIED_TTS_API_KEY=%s\n' "$(openssl rand -hex 32)"
  printf '%s\n' 'BOTIFIED_TTS_MODEL_SOURCE=modelscope'
  printf '%s\n' 'BOTIFIED_TTS_LOG_LEVEL=INFO'
} > botified-tts.env
```

`BOTIFIED_TTS_MODEL_SOURCE` is required and accepts only `modelscope` or
`huggingface`. Each source uses a fixed repository and immutable revision.
There is no automatic detection or cross-source fallback. The API key must be
unquoted and match `[A-Za-z0-9._~-]+`.

`BOTIFIED_TTS_SEGMENT_PROFILE` is optional and accepts `natural` or `short`.
The default `natural` profile uses 100/160-character target/hard limits for
fewer, more natural boundaries. `short` uses 55/80-character limits to reduce
the generation span, at the cost of more frequent boundaries that may sound
less natural. To select it, add this line to the env file before starting the
container:

```bash
printf '%s\n' 'BOTIFIED_TTS_SEGMENT_PROFILE=short' >> botified-tts.env
```

The selected profile is fixed at startup and applies to both HTTP and
WebSocket synthesis.

Start the fixed release image:

```bash
docker run -d \
  --name botified-tts \
  --restart on-failure:3 \
  --gpus '"device=0"' \
  --env-file ./botified-tts.env \
  -p 8000:8000 \
  -v botified-tts-data:/data \
  ghcr.io/lzjever/botified-tts:v0.2.2
```

Check readiness and failures with:

```bash
docker inspect --format '{{.State.Health.Status}}' botified-tts
docker logs botified-tts
```

The ready state is `healthy`. Registered voices and the selected source's model
cache persist in the `botified-tts-data` volume. Docker fails to start the
container if its GPU request cannot be satisfied or NVIDIA Container Toolkit
does not expose the device. If the container starts but the application CUDA
preflight fails, the application exits before model download or Nano worker
creation and logs `cuda_unavailable` or `cuda_device_invalid`.

## Integrate with Botified

The `tts` Skill targets Botified Core `v0.4.47` with its built-in `bash` tool
enabled. Its helper only requires Python 3.10 or newer and the Python standard
library; it does not require curl, third-party Python packages, CUDA, Torch, or
FFmpeg. CUDA remains a requirement of the TTS service host.

Check out this repository on the Core host and verify the helper runtime:

```bash
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'
```

Install the Skill as the Core service account. First resolve the Agent root:

- without `runtime.agents_dir`, use the Core service account's
  `$HOME/.agents`;
- use an absolute `runtime.agents_dir` directly;
- resolve a relative `runtime.agents_dir` from the Botified configuration
  file's directory.

Set `AGENTS_DIR` below to that resolved absolute path, then install the two
Skill files:

```bash
AGENTS_DIR=/absolute/path/to/resolved-agents-dir
: "${AGENTS_DIR:?set AGENTS_DIR to the resolved Agent root}"

install -d -m 0700 \
  "${AGENTS_DIR}/skills/tts/scripts" \
  "${AGENTS_DIR}/env.d"
install -m 0644 \
  skills/tts/SKILL.md \
  "${AGENTS_DIR}/skills/tts/SKILL.md"
install -m 0755 \
  skills/tts/scripts/botified-tts \
  "${AGENTS_DIR}/skills/tts/scripts/botified-tts"
```

The executable bit on `scripts/botified-tts` is required. Do not add this
Skill through `skills.explicit` or install another Skill named `tts`; duplicate
names make `$tts` ambiguous.

Create the Skill client's private configuration as an atomic update:

```bash
install -m 0600 /dev/null \
  "${AGENTS_DIR}/env.d/botified-tts.env.tmp"
# Write exactly these two literal, unquoted NAME=VALUE entries to the .tmp file:
# BOTIFIED_TTS_URL=http://tts-host:8000
# BOTIFIED_TTS_API_KEY=replace_with_actual_key
mv \
  "${AGENTS_DIR}/env.d/botified-tts.env.tmp" \
  "${AGENTS_DIR}/env.d/botified-tts.env"
```

The directory and file must be owned by the Core effective uid or root and
must not be writable by group or other. When root owns them, the Core account
must still be able to traverse the directories and read the file. Keep the URL
and key in this one file, define each name only once across `env.d/*.env`, and
do not add `export`, quotes, interpolation, or shell syntax.

Botified grants `env.d` variables to every new Core Bash process and managed
task; it is not per-Skill isolation or a service configuration mechanism. The
helper and companion both read `BOTIFIED_TTS_URL` and
`BOTIFIED_TTS_API_KEY` from their process environment. Neither locates or
parses `env.d`, and they do not share code or installation paths. Only the
Docker service keeps its separate explicit `botified-tts.env`; do not use the
Agent `env.d` file to configure the container.

An installed or updated Skill is rediscovered on the next fresh provider
request. An atomically replaced env file applies to the next Bash process or
new task. Restart a running companion task to pick up changed values. These
normal Skill and env updates do not require restarting Core.

Use the installed helper for health and voice profiles:

```bash
TTS="${AGENTS_DIR}/skills/tts/scripts/botified-tts"

"${TTS}" health
"${TTS}" voice-list
"${TTS}" voice-create \
  --name assistant \
  --file "${AGENT_PATH}" \
  --filename "${ORIGINAL_FILENAME}" \
  --prompt-text 'The exact words spoken in the reference.'
```

`voice-create` always requires both `--file` and `--filename`. For a Botified
file ref, pass its available `agent_path` as `--file` and its manifest
`filename` as `--filename`. The helper uses the filename only to select WAV,
FLAC, or MP3 and sends a fixed safe multipart filename; it does not copy the
input or store its original filename. Omit `--prompt-text` for controllable
cloning when no exact transcript is available. Faithful cloning requires a
transcript that exactly matches the reference recording.

Generate caller-facing files inside Botified's runtime cwd. Ogg/Opus is the
default choice for smaller attachments and voice messages:

```bash
"${TTS}" speak \
  --text '你好。' \
  --output reply.ogg
```

The Agent must publish generated files rather than return a server-local path.
For an ordinary attachment, call `publish_file` with:

```json
{
  "path": "reply.ogg",
  "filename": "reply.ogg",
  "mime_type": "audio/ogg"
}
```

For a voice-message presentation request, use:

```json
{
  "path": "reply.ogg",
  "filename": "reply.ogg",
  "mime_type": "audio/ogg",
  "audio_as_voice": true
}
```

`audio_as_voice: true` is a delivery hint; unsupported channels may present an
ordinary attachment. The complete synthesis, voice selection, text-tag, and
long-input workflow is in the installed [`tts` Skill](skills/tts/SKILL.md).

Botified Runtime Data variables are not used for TTS file delivery. The helper
writes the requested file in the runtime cwd and the Agent publishes it with
`publish_file`; the companion only streams audio to a local loudspeaker.

### Upgrade an older Skill installation

Upgrade Core to `v0.4.47`, install `skills/tts`, and create the `env.d` file
before removing the old `voxcpm-tts` Skill. Do not keep both Skills installed.
If the old checkout path appears in `skills.explicit`, stop Core through its
existing supervisor, remove only that old entry, and start Core again. A normal
Agent-root installation does not require a YAML change or restart.

The old helper `--env-file` option no longer exists, and every `voice-create`
call now requires `--filename`. The Docker service's explicit
`botified-tts.env` remains separate from the Agent `env.d` file.

### Companion

Install the companion's independent lightweight environment:

```bash
uv sync --project companions/botified --locked --no-dev
```

The optional companion turns Botified `stream_text` observations into live
audio on the Botified host's local loudspeaker and cancels playback on user
interruption, provider replacement, or stdin close. It is not part of a normal
TTS service deployment and does not start when installed.

Its profile and Voice Design task preset examples are in the
[companion README](companions/botified/README.md). They call the installed
`botified-tts-companion` command without a URL, API key, or env-file argument
and are registered with `start_on_boot: []`. Start one only when local playback
is wanted. Adding or changing a preset requires restarting Core through its
external supervisor; changing `env.d` only requires restarting the running
companion task.

## Develop

The local test suite additionally requires `ffmpeg` and `ffprobe`:

```bash
command -v ffmpeg ffprobe
uv sync --locked
uv run pytest -q
```

Optional source execution still requires CUDA:

```bash
BOTIFIED_TTS_DATA_DIR="$PWD/.data" \
  uv run --env-file ./botified-tts.env botified-tts
```

## Build the image

Power users can build from the repository root:

```bash
docker build --provenance=false --platform linux/amd64 -t botified-tts:local .
```

Run it with the same env file, GPU, port, and volume from the service command
above, replacing only `ghcr.io/lzjever/botified-tts:v0.2.2` with
`botified-tts:local`.

## API and capabilities

All authenticated endpoints use `Authorization: Bearer <API key>`.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Public readiness, CUDA, logical model name, and sample rate |
| `POST /v1/speech` | Synthesize a complete mono 48 kHz WAV or Ogg/Opus file |
| `POST /v1/voices` | Register a trusted reference voice |
| `GET /v1/voices` | List registered voice profiles |
| `DELETE /v1/voices/{voice_id}` | Delete a voice profile |
| `WS /v1/speech/stream` | Append text while receiving binary PCM audio |

Canonical synthesis options are top-level fields:

| Use | Options |
|---|---|
| Ordinary speech | `{}`, with optional `"style"` |
| Voice Design | `{"voice":{"type":"design","description":"A warm, natural voice"}}`, with optional `"style"` |
| Controllable clone | `{"voice":{"type":"profile","id":"voice_..."},"mode":"controllable"}`, with optional `"style"` |
| Faithful clone | `{"voice":{"type":"profile","id":"voice_..."},"mode":"faithful"}`; `"style"` is not accepted |

For `POST /v1/speech`, add the required top-level `"text"` field to the selected
options. WAV is the default. Send `Accept: audio/ogg` to receive Ogg/Opus:

```bash
curl \
  -H 'Authorization: Bearer <API key>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: audio/ogg' \
  --data '{"text":"你好。"}' \
  http://127.0.0.1:8000/v1/speech \
  --output speech.ogg
```

For WebSocket streaming, add the required top-level `"type":"start"` field to
the same options, then send `append` events followed by `finish` or `cancel`.

`POST /v1/voices` accepts multipart fields `name`, `file`, and optional
`prompt_text`. A profile used in faithful mode must have a `prompt_text` that
exactly matches the words spoken in its reference recording.

The service also supports VoxCPM2 native tags and cancellation, automatically
splits input into sentence-aware segments, and uses fixed request-level
conditioning to reduce accumulated voice drift across segments. It receives
final speakable plain text and does not parse Markdown or SSML. The bundled
[`tts` Skill](skills/tts/SKILL.md) provides the concise HTTP
workflow for synthesis and voice management. Its `speak` command requests WAV
or Ogg/Opus from the lowercase `.wav` or `.ogg` output suffix; it does not run
FFmpeg locally.

WebSocket clients send a `start` event, then any number of incremental `append`
events, followed by `finish` or `cancel`. The server returns JSON lifecycle
events and binary mono 48 kHz PCM s16le chunks. Text segmentation is internal,
so callers may append token-sized input or larger chunks.

HTTP text is limited to 16 KiB UTF-8. Send the complete speakable text for one
output file in one request; the service performs sentence-aware internal
segmentation and returns one complete WAV or Ogg. Each WebSocket append is
limited to 16 KiB and each session to 64 KiB. Reference uploads are limited to
25 MiB. Synthesis capacity is bounded at 16 concurrent requests; callers
should handle `service_busy`. Stable errors include `invalid_api_key`,
`invalid_request`, `invalid_voice`, `input_too_large`, `service_busy`,
`engine_error`, and WebSocket `client_too_slow`.
