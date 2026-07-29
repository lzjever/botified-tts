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
  ghcr.io/lzjever/botified-tts:v0.2.1
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

Botified integrators check out this repository on the Botified host. Configure
Botified `skills.explicit` to point directly to the checkout's
[`skills/voxcpm-tts/SKILL.md`](skills/voxcpm-tts/SKILL.md). Do not copy or
symlink the Skill.

Install the companion's independent lightweight environment:

```bash
uv sync --project companions/botified --locked --no-dev
```

The companion turns Botified `stream_text` observations into live audio and
cancels playback on user interruption, provider replacement, or stdin close.
Its profile + mode + style and Voice Design + style task preset examples are in
the [companion README](companions/botified/README.md). Both use the checkout's
absolute `botified-tts.env` path and the complete WebSocket endpoint.

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
above, replacing only `ghcr.io/lzjever/botified-tts:v0.2.1` with
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
| Ordinary speech | `{}` |
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
[`voxcpm-tts` Skill](skills/voxcpm-tts/SKILL.md) provides the concise HTTP
workflow for synthesis and voice management. Its `speak` command requests WAV
or Ogg/Opus from the lowercase `.wav` or `.ogg` output suffix; it does not run
FFmpeg locally.

WebSocket clients send a `start` event, then any number of incremental `append`
events, followed by `finish` or `cancel`. The server returns JSON lifecycle
events and binary mono 48 kHz PCM s16le chunks. Text segmentation is internal,
so callers may append token-sized input or larger chunks.

HTTP text is limited to 8 KiB UTF-8. Each WebSocket append is limited to 16 KiB
and each session to 64 KiB. Reference uploads are limited to 25 MiB. Synthesis
capacity is bounded at 16 concurrent requests; callers should handle
`service_busy`. Stable errors include `invalid_api_key`, `invalid_request`,
`invalid_voice`, `input_too_large`, `service_busy`, `engine_error`, and
WebSocket `client_too_slow`.
