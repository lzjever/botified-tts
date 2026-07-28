# Botified TTS

Botified TTS is a standalone VoxCPM2 service for Botified. It supports ordinary
speech, Voice Design, controllable and faithful voice cloning, style and native
VoxCPM2 tags, complete HTTP WAV responses, and bidirectional WebSocket
streaming.

The first release supports Linux x86_64, one selected NVIDIA GPU, a CUDA
12-compatible driver, Docker Compose, and NVIDIA Container Toolkit. There is no
CPU, ROCm, Windows, Apple Silicon, or multi-GPU fallback. If CUDA is unavailable
or the selected GPU is invalid, deployment exits during the host GPU and NVIDIA
container runtime preflight, before building. After the image is built, the
application checks PyTorch CUDA before downloading the model or creating a Nano
worker.

## Deploy

The only deployment command is:

```bash
./scripts/deploy.sh
```

On first use it creates `deploy/.env` with mode `0600`, a random 32-byte API
key, `HOST_GPU=0`, and `PUBLISHED_PORT=8000`. To select another physical GPU or
host port, edit only those values and keep the file private:

```bash
chmod 0600 deploy/.env
${EDITOR:-vi} deploy/.env
```

Do not commit `deploy/.env` or put the API key in command arguments. Read it
without evaluating the file:

```bash
API_KEY_FILE=deploy/api-key
(umask 077; awk -F= '$1 == "BOTIFIED_TTS_API_KEY" { print substr($0, index($0, "=") + 1) }' \
  deploy/.env > "${API_KEY_FILE}")
```

Compose stores registered voices and the pinned model cache in the named volume
mounted at `/data` (`/data/voices` and `/data/model-cache`), so container
rebuilds preserve both.

Check readiness without authentication:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
```

## HTTP and voice profiles

The HTTP speech endpoint returns mono 48 kHz PCM s16le WAV. Pass the Bearer
header over standard input so the key does not appear in `curl` arguments:

```bash
set -o pipefail
awk '{ print "Authorization: Bearer " $0 }' "${API_KEY_FILE}" |
  curl --disable --fail-with-body --silent --show-error \
    --proto '=http,https' \
    --header @- \
    --header 'Content-Type: application/json' \
    --data-binary '{"text":"你好，这是 Botified TTS。"}' \
    --output hello.wav \
    http://127.0.0.1:8000/v1/speech
```

For voice profiles and the four synthesis modes, use the bundled helper:

```bash
export BOTIFIED_TTS_URL=http://127.0.0.1:8000
API_KEY_FILE=deploy/api-key
TTS=./skills/voxcpm-tts/scripts/botified-tts

"${TTS}" --api-key-file "${API_KEY_FILE}" voice-create \
  --name assistant --file reference.wav \
  --prompt-text 'The exact words spoken in the reference.'
"${TTS}" --api-key-file "${API_KEY_FILE}" voice-list

"${TTS}" --api-key-file "${API_KEY_FILE}" speak \
  --text 'Normal speech.' --output normal.wav
"${TTS}" --api-key-file "${API_KEY_FILE}" speak \
  --text 'Designed speech.' --output design.wav \
  --design 'A warm, natural voice'
"${TTS}" --api-key-file "${API_KEY_FILE}" speak \
  --text 'Controllable clone.' --output clone.wav \
  --voice-id voice_0123456789abcdef0123456789abcdef --style 'calm'
"${TTS}" --api-key-file "${API_KEY_FILE}" speak \
  --text 'Faithful clone.' --output faithful.wav \
  --voice-id voice_0123456789abcdef0123456789abcdef --mode faithful
```

`voice-create` prints the real voice ID to use. Its optional prompt text must
exactly match the reference recording and is required before faithful mode can
use that profile. The helper also provides `health` and
`voice-delete --id <voice_id>`, and refuses to overwrite an output file. See
[`skills/voxcpm-tts/SKILL.md`](skills/voxcpm-tts/SKILL.md) for the concise
Agent workflow. Its raw key file must contain one non-empty ASCII line; the
helper does not read `BOTIFIED_TTS_API_KEY`. Pass final, already speakable plain
text; neither the helper nor the service parses Markdown or SSML.

For explicit use, point Botified's agent at this repository's
`skills/voxcpm-tts` directory and invoke `$voxcpm-tts`. For workspace discovery,
install that same directory at `.agents/skills/voxcpm-tts` in the Botified
workspace. Keep `skills/voxcpm-tts` as the single source and do not create
another helper implementation.

## Bidirectional streaming

The WebSocket accepts incremental text while returning binary mono 48 kHz PCM
s16le chunks. The server performs sentence-aware segmentation internally. This
minimal client writes the raw stream into a WAV file:

```python
import asyncio
import json
import os
import wave

from websockets.asyncio.client import connect


async def receive_audio(websocket, pcm: bytearray) -> dict:
    while True:
        message = await websocket.recv()
        if isinstance(message, bytes):
            pcm.extend(message)
            continue
        event = json.loads(message)
        if event.get("type") == "error":
            raise RuntimeError(event)
        if event.get("type") == "done":
            return event
        raise RuntimeError(f"unexpected event: {event}")


async def main() -> None:
    base = os.environ["BOTIFIED_TTS_URL"].rstrip("/")
    scheme = "wss" if base.startswith("https://") else "ws"
    uri = f"{scheme}://{base.split('://', 1)[1]}/v1/speech/stream"
    headers = {"Authorization": f"Bearer {os.environ['BOTIFIED_TTS_API_KEY']}"}
    pcm = bytearray()

    async with connect(uri, additional_headers=headers) as websocket:
        await websocket.send(json.dumps({"type": "start"}))
        ready = json.loads(await websocket.recv())
        if ready.get("type") != "ready":
            raise RuntimeError(ready)

        async with asyncio.TaskGroup() as tasks:
            receiver = tasks.create_task(receive_audio(websocket, pcm))
            for text in ("你好，", "这是逐块输入。", "服务会同时流式返回声音。"):
                await websocket.send(json.dumps({"type": "append", "text": text}))
            await websocket.send(json.dumps({"type": "finish"}))
            # On user interruption, send {"type": "cancel"} instead of finish.
        done = receiver.result()
        if done.get("cancelled"):
            print("stream cancelled")

    with wave.open("stream.wav", "wb") as output:
        output.setnchannels(ready["audio"]["channels"])
        output.setsampwidth(2)
        output.setframerate(ready["audio"]["sample_rate"])
        output.writeframes(pcm)


asyncio.run(main())
```

This example needs the external `websockets` package; it is not a service
dependency.

## Limits, errors, and configuration

HTTP text is limited to 8 KiB UTF-8. A WebSocket `append` is limited to 16 KiB
and a session to 64 KiB. Reference uploads are limited to 25 MiB. Capacity is
bounded; callers should handle `service_busy`. Stable errors include
`invalid_api_key`, `invalid_request`, `invalid_voice`, `input_too_large`,
`service_busy`, `engine_error`, and WebSocket `client_too_slow`. REST errors use
a JSON error envelope; WebSocket errors are JSON events. Cancel completes as
`{"type":"done","cancelled":true}`.

The application reads exactly these eight environment variables:

```text
BOTIFIED_TTS_HOST=0.0.0.0
BOTIFIED_TTS_PORT=8000
BOTIFIED_TTS_MODEL=openbmb/VoxCPM2
BOTIFIED_TTS_MODEL_REVISION=<immutable-revision>
BOTIFIED_TTS_GPU_DEVICE=0
BOTIFIED_TTS_DATA_DIR=/data
BOTIFIED_TTS_API_KEY=<secret>
BOTIFIED_TTS_LOG_LEVEL=INFO
```

`HOST_GPU` and `PUBLISHED_PORT` are separate deployment-only variables from
`deploy/.env`; they are not injected into the application.

For focused development checks:

```bash
uv run pytest -q tests/test_api.py tests/test_streaming.py tests/test_skill_helper.py
```

On a CUDA development machine, run the focused real-engine smoke from the
repository root:

```bash
uv run python tests/gpu_smoke.py \
  --data-dir /var/tmp/botified-tts-gpu-smoke/data \
  --output-dir /var/tmp/botified-tts-gpu-smoke/output
```

`--data-dir` holds the model cache and temporary runtime data.
`--output-dir` receives the generated WAV files. The script covers CUDA
preflight, model loading and warmup, ordinary speech, Voice Design, both clone
modes, style, native tags, continuation, incremental text before `finish`,
stream cancellation, fixed PCM chunks, RTF, and idle Nano child failure.
