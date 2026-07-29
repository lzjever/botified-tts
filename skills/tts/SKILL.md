---
name: tts
description: Use the configured Botified TTS service for multilingual speech synthesis, voice design and cloning, expressive style and VoxCPM2 nonverbal tags, WAV/Ogg publication, and trusted voice profile management.
when_to_use: TTS; text to speech; 文字转语音; 朗读; 发送或回复语音; 音色设计或克隆; 情绪、语速、语气词、笑声或停顿; 多语言或方言语音; 管理音色
---

# TTS

Use Botified `bash` to run `scripts/botified-tts` relative to this `SKILL.md`.
Use only that helper; do not reimplement its HTTP calls.

## Runtime check

Resolve the helper first:

```bash
TTS=<skill-directory>/scripts/botified-tts
```

Before use, verify the helper is executable and the host has Python 3.10 or
newer. The helper uses only the Python standard library. If either prerequisite
is absent, report it; do not install dependencies or replace the helper with
ad hoc HTTP.

Configuration comes from Botified `<resolved-agents-dir>/env.d/*.env` and is
globally visible to every Botified Bash process, not isolated to this Skill. Before every helper call, check the URL without printing its value:

```bash
test "${BOTIFIED_TTS_URL+x}" = x
```

After that check, `health` can run without an API key:

```bash
"${TTS}" health
```

Before `voice-create`, `voice-list`, `voice-delete`, or `speak`, also check the API key without printing its value:

```bash
test "${BOTIFIED_TTS_API_KEY+x}" = x
```

Never echo, log, or pass the key as an argument. Do not read or modify `env.d`.

## Choose one synthesis mode

| User intent | Use |
| --- | --- |
| Read with the default voice | `speak` without `--design` or `--voice-id`; optionally add `--style` for emotion, pace, volume, or rhythm |
| Create a voice from a description | Add `--design` for identity, voice texture, and use case; optionally add `--style` for this utterance |
| Preserve a registered voice while changing expression | Add `--voice-id`; use the default controllable mode and optionally `--style` |
| Continue the reference voice, rhythm, emotion, and style as faithfully as possible | Add `--voice-id` and `--mode faithful`; never add `--style` |

The default voice and Voice Design do not promise the same identity across requests. Create and reuse a voice profile when identity must persist.

Use the matching helper form:

```bash
"${TTS}" speak --text "${TEXT}" --output reply.ogg
"${TTS}" speak --text "${TEXT}" --output reply.ogg --style "${STYLE}"
"${TTS}" speak --text "${TEXT}" --output reply.ogg \
  --design "${VOICE_DESCRIPTION}" --style "${STYLE}"
"${TTS}" speak --text "${TEXT}" --output reply.ogg \
  --voice-id "${VOICE_ID}" --style "${STYLE}"
"${TTS}" speak --text "${TEXT}" --output reply.ogg \
  --voice-id "${VOICE_ID}" --mode faithful
```

Omit an unused optional flag instead of passing an empty value.

## Manage voice profiles

Use a clean, single-speaker WAV, FLAC, or MP3 reference. Prefer 5–30 seconds
with stable speech and little background noise.

For an attached reference, require an available Botified file ref containing
both `agent_path` and `filename`. If it is unavailable or lacks `agent_path`,
ask the user to upload it again. Pass the manifest fields directly:

```bash
"${TTS}" voice-create \
  --name "${PROFILE_NAME}" \
  --file "${AGENT_PATH}" \
  --filename "${MANIFEST_FILENAME}"
```

`--filename` is required even though `agent_path` has no extension. Supply
`--prompt-text "${EXACT_TRANSCRIPT}"` only when it is the exact word-for-word
transcript of the reference. Do not guess it and do not call ASR from this
Skill. Without an exact transcript, omit `--prompt-text` and use controllable
mode; faithful mode requires the exact transcript.

Read the creation response JSON field `id`, report it, and use it later as
`--voice-id`. To resolve a profile name, call:

```bash
"${TTS}" voice-list
```

Do not guess when a name has zero or multiple matches; ask the user to select
an `id`. Delete only the requested ID:

```bash
"${TTS}" voice-delete --id "${VOICE_ID}"
```

## Prepare speakable text

Pass final plain text only. Do not pass Markdown or SSML.

VoxCPM2 supports Arabic, Burmese, Chinese, Danish, Dutch, English, Finnish,
French, German, Greek, Hebrew, Hindi, Indonesian, Italian, Japanese, Khmer,
Korean, Lao, Malay, Norwegian, Polish, Portuguese, Russian, Spanish, Swahili,
Swedish, Filipino, Thai, Turkish, and Vietnamese. Write the target language
directly; do not add language tags.

For Chinese dialects, use authentic wording for Sichuanese, Cantonese, Wu,
Northeastern Mandarin, Henan, Shaanxi, Shandong, Tianjin, or Hokkien. Keep any
dialect instruction simple; do not expect a dialect name to transform standard
Mandarin wording by itself.

Use punctuation as a prosody cue: periods and question marks mark stronger
sentence endings, commas mark shorter pauses, and ellipses may signal
hesitation or trailing speech. Prefer short natural sentences over stacked
punctuation. Very short fragments may sound weak; combine adjacent content
into a natural sentence only when meaning is unchanged.

Write numbers, dates, units, and abbreviations in the exact form that should be
spoken. Resolve pronunciation ambiguity without changing names, amounts,
dates, or other facts.

Use only these 11 stable recommended nonverbal tags, preserving spelling and
case:

```text
[laughing] [sigh] [Uhm] [Shh]
[Question-ah] [Question-ei] [Question-en] [Question-oh]
[Surprise-wa] [Surprise-yo] [Dissatisfaction-hnn]
```

Place a tag where the expression should occur and use tags sparingly. Do not
invent variants or stack many tags. These are the stable recommended set, not
a complete model vocabulary; do not advertise or proactively use other
bracketed forms. The helper passes tags unchanged.

## Handle length and publish

Send one complete speakable response intended to become one audio file in one
`speak` call. The service accepts at most 16384 UTF-8 bytes and performs
sentence-aware internal segmentation. Do not estimate the byte size, truncate,
pre-split, or automatically retry the text.

If the service returns `input_too_large`, report the 16 KiB limit and ask the
user to shorten the response. Do not create numbered parts or multiple
independent files.

Create every result as a new relative `.wav` or `.ogg` regular file under the
runtime cwd. Never overwrite, use an absolute or `..` path, write outside cwd,
or publish a symlink. Default to Ogg/Opus unless the user explicitly requests
WAV.

Publish every caller-facing result with Botified `publish_file`; never return
only a server-local path and do not upload it through Botified Runtime Data.
Call `publish_file` once for the one generated file. If publication fails or
its result is unknown, report the failure and stop; do not retry, read channel
configuration or credentials, use an access token, or call a channel API
directly. For a normal attachment, match the MIME type and omit
`audio_as_voice`:

```json
{"path":"reply.ogg","filename":"reply.ogg","mime_type":"audio/ogg"}
{"path":"reply.wav","filename":"reply.wav","mime_type":"audio/wav"}
```

For a voice-message request, generate Ogg/Opus and publish:

```json
{"path":"reply.ogg","filename":"reply.ogg","mime_type":"audio/ogg","audio_as_voice":true}
```

`audio_as_voice: true` requests compatible channel presentation; it does not
guarantee that every channel renders a native voice message.

## Boundaries

Do not use this Skill for ASR, transcription, analysis, or playback of existing
audio. Do not invent helper flags, API fields, or environment variables for
unexposed upstream CFG, inference steps, seed, normalization, denoise,
bad-case retry, phonemes, LoRA, batch, or timestamps. Token streaming and
realtime playback belong to this repository's Botified companion, not this
file-oriented helper.
