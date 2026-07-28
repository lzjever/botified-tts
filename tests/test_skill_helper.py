from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import wave
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest


ROOT = Path(__file__).parents[1]
HELPER = ROOT / "skills/voxcpm-tts/scripts/botified-tts"
VOICE_ID = "voice_" + "1" * 32


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48_000)
        audio.writeframes(b"\x00\x00")
    return output.getvalue()


def _ogg() -> bytes:
    return b"OggS" + b"\x00" * 24 + b"OpusHead" + b"\x00" * 16


class _Server(ThreadingHTTPServer):
    records: list[tuple[str, str, dict[str, str], bytes]]
    fail_speech: bool
    speech_response: tuple[bytes, str] | None


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def _record(self) -> bytes:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.records.append(
            (
                self.command,
                self.path,
                dict(self.headers.items()),
                body,
            )
        )
        return body

    def _send(
        self,
        status: int,
        body: bytes = b"",
        content_type: str | None = None,
    ) -> None:
        self.send_response(status)
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._record()
        if self.path == "/health":
            self._send(200, b'{"status":"ready"}', "application/json")
        elif self.path == "/v1/voices":
            self._send(200, b'{"object":"list","data":[]}', "application/json")
        else:
            self._send(404)

    def do_POST(self) -> None:
        self._record()
        if self.path == "/v1/voices":
            self._send(201, json.dumps({"id": VOICE_ID}).encode(), "application/json")
        elif self.path == "/v1/speech" and self.server.fail_speech:
            self._send(
                400,
                b'{"error":{"code":"invalid_request"}}',
                "application/json",
            )
        elif self.path == "/v1/speech":
            if self.server.speech_response is not None:
                body, content_type = self.server.speech_response
            elif self.headers.get("Accept") == "audio/ogg":
                body, content_type = _ogg(), "audio/ogg"
            else:
                body, content_type = _wav(), "audio/wav"
            self._send(200, body, content_type)
        else:
            self._send(404)

    def do_DELETE(self) -> None:
        self._record()
        self._send(204)

    def log_message(self, _format: str, *args: object) -> None:
        pass


@contextmanager
def _service() -> Iterator[tuple[_Server, str]]:
    server = _Server(("127.0.0.1", 0), _Handler)
    server.records = []
    server.fail_speech = False
    server.speech_response = None
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _run(
    url: str,
    *args: str,
    env_file: Path | None = None,
    include_env_file: bool = True,
    environment_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["BOTIFIED_TTS_URL"] = url
    environment["BOTIFIED_TTS_API_KEY"] = "ignored-legacy-key"
    if environment_overrides:
        environment.update(environment_overrides)
    command = [str(HELPER)]
    if include_env_file:
        assert env_file is not None
        command.extend(("--env-file", str(env_file)))
    command.extend(args)
    return subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _multipart(body: bytes, content_type: str) -> dict[str, bytes]:
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: "
        + content_type.encode()
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(
            decode=True
        )
        for part in message.iter_parts()
    }


def test_helper_routes_commands_and_all_speak_modes(tmp_path: Path) -> None:
    env_file = tmp_path / "botified-tts.env"
    env_file.write_text(
        "BOTIFIED_TTS_MODEL_SOURCE=modelscope\n"
        "BOTIFIED_TTS_API_KEY=test-key\n"
        "BOTIFIED_TTS_LOG_LEVEL=INFO\n",
        encoding="ascii",
    )
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference-audio")
    outputs = [
        tmp_path / f"{name}.wav" for name in ("normal", "design", "clone", "faithful")
    ]
    ogg_output = tmp_path / "publish.ogg"

    with _service() as (server, url):
        health = _run(url, "health", env_file=env_file)
        created = _run(
            url,
            "voice-create",
            "--name",
            "assistant",
            "--file",
            str(reference),
            "--prompt-text",
            "精确文本",
            env_file=env_file,
        )
        listed = _run(url, "voice-list", env_file=env_file)
        deleted = _run(
            url,
            "voice-delete",
            "--id",
            VOICE_ID,
            env_file=env_file,
        )
        spoken = [
            _run(
                url,
                "speak",
                "--text",
                "normal",
                "--output",
                str(outputs[0]),
                "--style",
                "calm",
                env_file=env_file,
            ),
            _run(
                url,
                "speak",
                "--text",
                "design",
                "--output",
                str(outputs[1]),
                "--design",
                "warm voice",
                "--style",
                "slow",
                env_file=env_file,
            ),
            _run(
                url,
                "speak",
                "--text",
                "clone",
                "--output",
                str(outputs[2]),
                "--voice-id",
                VOICE_ID,
                env_file=env_file,
            ),
            _run(
                url,
                "speak",
                "--text",
                "faithful",
                "--output",
                str(outputs[3]),
                "--voice-id",
                VOICE_ID,
                "--mode",
                "faithful",
                env_file=env_file,
            ),
        ]
        ogg = _run(
            url,
            "speak",
            "--text",
            "publish",
            "--output",
            str(ogg_output),
            env_file=env_file,
        )

    assert all(
        result.returncode == 0
        for result in (health, created, listed, deleted, *spoken, ogg)
    )
    assert json.loads(health.stdout) == {"status": "ready"}
    assert json.loads(created.stdout) == {"id": VOICE_ID}
    assert json.loads(listed.stdout) == {"object": "list", "data": []}
    assert deleted.stdout.strip() == f"deleted {VOICE_ID}"
    assert [result.stdout.strip() for result in spoken] == [
        str(path) for path in outputs
    ]
    assert ogg.stdout.strip() == str(ogg_output)
    assert all(path.read_bytes() == _wav() for path in outputs)
    assert ogg_output.read_bytes() == _ogg()
    assert all(
        "test-key" not in result.stdout + result.stderr
        for result in (health, created, listed, deleted, *spoken, ogg)
    )

    assert [(method, path) for method, path, _, _ in server.records] == [
        ("GET", "/health"),
        ("POST", "/v1/voices"),
        ("GET", "/v1/voices"),
        ("DELETE", f"/v1/voices/{VOICE_ID}"),
        ("POST", "/v1/speech"),
        ("POST", "/v1/speech"),
        ("POST", "/v1/speech"),
        ("POST", "/v1/speech"),
        ("POST", "/v1/speech"),
    ]
    assert "Authorization" not in server.records[0][2]
    assert all(
        headers["Authorization"] == "Bearer test-key"
        for _, _, headers, _ in server.records[1:]
    )
    assert [record[2]["Accept"] for record in server.records[4:]] == [
        "audio/wav",
        "audio/wav",
        "audio/wav",
        "audio/wav",
        "audio/ogg",
    ]
    form = _multipart(
        server.records[1][3],
        server.records[1][2]["Content-Type"],
    )
    assert form == {
        "name": b"assistant",
        "file": b"reference-audio",
        "prompt_text": "精确文本".encode(),
    }
    assert [json.loads(record[3]) for record in server.records[4:]] == [
        {"text": "normal", "style": "calm"},
        {
            "text": "design",
            "voice": {"type": "design", "description": "warm voice"},
            "style": "slow",
        },
        {
            "text": "clone",
            "voice": {"type": "profile", "id": VOICE_ID},
            "mode": "controllable",
        },
        {
            "text": "faithful",
            "voice": {"type": "profile", "id": VOICE_ID},
            "mode": "faithful",
        },
        {"text": "publish"},
    ]


def test_helper_rejects_invalid_arguments_and_keeps_output_atomic(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "botified-tts.env"
    env_file.write_text("BOTIFIED_TTS_API_KEY=test-key\n", encoding="ascii")
    output = tmp_path / "speech.wav"
    unsupported_output = tmp_path / "speech.mp3"
    invalid = [
        (
            "speak",
            "--text",
            "x",
            "--output",
            str(output),
            "--design",
            "x",
            "--voice-id",
            VOICE_ID,
        ),
        ("speak", "--text", "x", "--output", str(output), "--mode", "faithful"),
        (
            "speak",
            "--text",
            "x",
            "--output",
            str(output),
            "--voice-id",
            VOICE_ID,
            "--mode",
            "faithful",
            "--style",
            "calm",
        ),
        ("speak", "--text", "x", "--output", str(output), "--voice-id", "bad"),
        ("speak", "--text", "x"),
        ("speak", "--text", "x", "--output", str(unsupported_output)),
        ("voice-list", "--unknown"),
        ("speak", "--text", "one", "--text", "two", "--output", str(output)),
    ]

    with _service() as (server, url):
        for args in invalid:
            assert _run(url, *args, env_file=env_file).returncode != 0
        assert server.records == []

        output.write_bytes(b"keep")
        assert (
            _run(
                url,
                "speak",
                "--text",
                "x",
                "--output",
                str(output),
                env_file=env_file,
            ).returncode
            != 0
        )
        assert output.read_bytes() == b"keep"
        assert server.records == []
        output.unlink()

        assert _run("", "health", env_file=env_file).returncode != 0
        missing_flag = _run(url, "voice-list", include_env_file=False)
        assert missing_flag.returncode != 0
        assert "--env-file is required" in missing_flag.stderr
        legacy_flag = _run(
            url,
            "--api-key-file",
            str(env_file),
            "voice-list",
            include_env_file=False,
        )
        assert legacy_flag.returncode != 0
        assert "unknown global argument: --api-key-file" in legacy_flag.stderr
        assert server.records == []

        server.fail_speech = True
        failed = _run(
            url,
            "speak",
            "--text",
            "x",
            "--output",
            str(output),
            env_file=env_file,
        )

    assert failed.returncode != 0
    assert "invalid_request" in failed.stderr
    assert not output.exists()
    assert list(tmp_path.glob(".speech.wav.tmp.*")) == []


@pytest.mark.parametrize(
    ("body", "content_type", "message"),
    (
        (_ogg(), "audio/wav", "speech response is not audio/ogg"),
        (b"OggS without an Opus identification header", "audio/ogg", "valid Ogg/Opus"),
    ),
    ids=("mime-mismatch", "invalid-ogg"),
)
def test_helper_rejects_invalid_ogg_response_atomically(
    tmp_path: Path,
    body: bytes,
    content_type: str,
    message: str,
) -> None:
    env_file = tmp_path / "botified-tts.env"
    env_file.write_text("BOTIFIED_TTS_API_KEY=test-key\n", encoding="ascii")
    output = tmp_path / "speech.ogg"

    with _service() as (server, url):
        server.speech_response = (body, content_type)
        result = _run(
            url,
            "speak",
            "--text",
            "x",
            "--output",
            str(output),
            env_file=env_file,
        )

    assert result.returncode != 0
    assert message in result.stderr
    assert not output.exists()
    assert list(tmp_path.glob(".speech.ogg.tmp.*")) == []


def test_helper_rejects_invalid_env_files_without_evaluating_them(
    tmp_path: Path,
) -> None:
    shell_marker = tmp_path / "shell-was-evaluated"
    invalid_files = {
        "missing-file": (tmp_path / "missing", "must be a readable regular file"),
        "missing-key": (
            tmp_path / "missing-key",
            "must contain exactly one BOTIFIED_TTS_API_KEY entry",
        ),
        "duplicate-key": (
            tmp_path / "duplicate-key",
            "must contain exactly one BOTIFIED_TTS_API_KEY entry",
        ),
        "empty-key": (
            tmp_path / "empty-key",
            "BOTIFIED_TTS_API_KEY must match",
        ),
        "quoted-key": (
            tmp_path / "quoted-key",
            "BOTIFIED_TTS_API_KEY must match",
        ),
        "illegal-key": (
            tmp_path / "illegal-key",
            "BOTIFIED_TTS_API_KEY must match",
        ),
        "shell-text": (
            tmp_path / "shell-text",
            "BOTIFIED_TTS_API_KEY must match",
        ),
    }
    (tmp_path / "missing-key").write_text(
        "BOTIFIED_TTS_MODEL_SOURCE=modelscope\n",
        encoding="ascii",
    )
    (tmp_path / "duplicate-key").write_text(
        "BOTIFIED_TTS_API_KEY=first\nBOTIFIED_TTS_API_KEY=second\n",
        encoding="ascii",
    )
    (tmp_path / "empty-key").write_text(
        "BOTIFIED_TTS_API_KEY=\n",
        encoding="ascii",
    )
    (tmp_path / "quoted-key").write_text(
        'BOTIFIED_TTS_API_KEY="quoted"\n',
        encoding="ascii",
    )
    (tmp_path / "illegal-key").write_text(
        "BOTIFIED_TTS_API_KEY=bad value\n",
        encoding="ascii",
    )
    (tmp_path / "shell-text").write_text(
        f"BOTIFIED_TTS_API_KEY=$(touch {shell_marker})\n",
        encoding="ascii",
    )

    with _service() as (server, url):
        for path, message in invalid_files.values():
            result = _run(url, "voice-list", env_file=path)
            assert result.returncode != 0
            assert message in result.stderr
            output = result.stdout + result.stderr
            assert all(
                value not in output
                for value in ("first", "second", "quoted", "bad value", "$(touch")
            )
        assert server.records == []
    assert not shell_marker.exists()


@pytest.mark.parametrize("line_ending", [b"\r\n", b"\r"])
def test_helper_rejects_carriage_return_in_literal_api_key_value(
    tmp_path: Path,
    line_ending: bytes,
) -> None:
    api_key = b"secret-with-carriage-return"
    env_file = tmp_path / "botified-tts.env"
    env_file.write_bytes(b"BOTIFIED_TTS_API_KEY=" + api_key + line_ending)

    with _service() as (server, url):
        result = _run(url, "voice-list", env_file=env_file)

    assert result.returncode != 0
    assert "BOTIFIED_TTS_API_KEY must match" in result.stderr
    assert api_key.decode() not in result.stdout + result.stderr
    assert server.records == []


def test_helper_sends_bearer_header_over_stdin_not_argv(tmp_path: Path) -> None:
    api_key = "Aa09._~-secret-not-in-argv"
    env_file = tmp_path / "botified-tts.env"
    env_file.write_text(
        f"BOTIFIED_TTS_API_KEY={api_key}\n"
        "BOTIFIED_TTS_MODEL_SOURCE=huggingface\n",
        encoding="ascii",
    )
    curl_arguments = tmp_path / "curl-arguments"
    curl_stdin = tmp_path / "curl-stdin"
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    fake_curl = executable_dir / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\0\' "$@" > "${CURL_ARGUMENTS_FILE}"\n'
        'cat > "${CURL_STDIN_FILE}"\n'
        'printf \'{"object":"list","data":[]}\'\n',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    result = _run(
        "http://127.0.0.1:8000",
        "voice-list",
        env_file=env_file,
        environment_overrides={
            "PATH": f"{executable_dir}:{os.environ['PATH']}",
            "CURL_ARGUMENTS_FILE": str(curl_arguments),
            "CURL_STDIN_FILE": str(curl_stdin),
        },
    )

    assert result.returncode == 0
    assert api_key.encode() not in curl_arguments.read_bytes()
    assert (
        curl_stdin.read_text(encoding="ascii") == f"Authorization: Bearer {api_key}\n"
    )
    assert api_key not in result.stdout + result.stderr
