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


class _Server(ThreadingHTTPServer):
    records: list[tuple[str, str, dict[str, str], bytes]]
    fail_speech: bool


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
            self._send(200, _wav(), "audio/wav")
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
    api_key_file: Path | None = None,
    include_api_key_file: bool = True,
    environment_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["BOTIFIED_TTS_URL"] = url
    environment["BOTIFIED_TTS_API_KEY"] = "ignored-legacy-key"
    if environment_overrides:
        environment.update(environment_overrides)
    command = [str(HELPER)]
    if include_api_key_file:
        assert api_key_file is not None
        command.extend(("--api-key-file", str(api_key_file)))
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
    api_key_file = tmp_path / "api-key"
    api_key_file.write_bytes(b"test-key\n")
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference-audio")
    outputs = [
        tmp_path / f"{name}.wav" for name in ("normal", "design", "clone", "faithful")
    ]

    with _service() as (server, url):
        health = _run(url, "health", api_key_file=api_key_file)
        created = _run(
            url,
            "voice-create",
            "--name",
            "assistant",
            "--file",
            str(reference),
            "--prompt-text",
            "精确文本",
            api_key_file=api_key_file,
        )
        listed = _run(url, "voice-list", api_key_file=api_key_file)
        deleted = _run(
            url,
            "voice-delete",
            "--id",
            VOICE_ID,
            api_key_file=api_key_file,
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
                api_key_file=api_key_file,
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
                api_key_file=api_key_file,
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
                api_key_file=api_key_file,
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
                api_key_file=api_key_file,
            ),
        ]

    assert all(
        result.returncode == 0 for result in (health, created, listed, deleted, *spoken)
    )
    assert json.loads(health.stdout) == {"status": "ready"}
    assert json.loads(created.stdout) == {"id": VOICE_ID}
    assert json.loads(listed.stdout) == {"object": "list", "data": []}
    assert deleted.stdout.strip() == f"deleted {VOICE_ID}"
    assert [result.stdout.strip() for result in spoken] == [
        str(path) for path in outputs
    ]
    assert all(path.read_bytes() == _wav() for path in outputs)
    assert all(
        "test-key" not in result.stdout + result.stderr
        for result in (health, created, listed, deleted, *spoken)
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
    ]
    assert "Authorization" not in server.records[0][2]
    assert all(
        headers["Authorization"] == "Bearer test-key"
        for _, _, headers, _ in server.records[1:]
    )
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
    ]


def test_helper_rejects_invalid_arguments_and_keeps_output_atomic(
    tmp_path: Path,
) -> None:
    api_key_file = tmp_path / "api-key"
    api_key_file.write_bytes(b"test-key\n")
    output = tmp_path / "speech.wav"
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
        ("voice-list", "--unknown"),
        ("speak", "--text", "one", "--text", "two", "--output", str(output)),
    ]

    with _service() as (server, url):
        for args in invalid:
            assert _run(url, *args, api_key_file=api_key_file).returncode != 0
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
                api_key_file=api_key_file,
            ).returncode
            != 0
        )
        assert output.read_bytes() == b"keep"
        assert server.records == []
        output.unlink()

        assert _run("", "health", api_key_file=api_key_file).returncode != 0
        missing_flag = _run(url, "voice-list", include_api_key_file=False)
        assert missing_flag.returncode != 0
        assert "--api-key-file is required" in missing_flag.stderr
        assert server.records == []

        server.fail_speech = True
        failed = _run(
            url,
            "speak",
            "--text",
            "x",
            "--output",
            str(output),
            api_key_file=api_key_file,
        )

    assert failed.returncode != 0
    assert "invalid_request" in failed.stderr
    assert not output.exists()
    assert list(tmp_path.glob(".speech.wav.tmp.*")) == []


def test_helper_rejects_invalid_api_key_files(tmp_path: Path) -> None:
    invalid_files = {
        "missing": (tmp_path / "missing", "must be a readable regular file"),
        "empty": (tmp_path / "empty", "must contain one non-empty ASCII line"),
        "leading-space": (
            tmp_path / "leading-space",
            "must not have leading or trailing whitespace",
        ),
        "crlf": (tmp_path / "crlf", "must contain one non-empty ASCII line"),
        "multiple-lines": (
            tmp_path / "multiple-lines",
            "must contain one non-empty ASCII line",
        ),
        "non-ascii": (tmp_path / "non-ascii", "must contain only ASCII"),
    }
    (tmp_path / "empty").write_bytes(b"")
    (tmp_path / "leading-space").write_bytes(b" key\n")
    (tmp_path / "crlf").write_bytes(b"key\r\n")
    (tmp_path / "multiple-lines").write_bytes(b"key\nother\n")
    (tmp_path / "non-ascii").write_bytes("密钥\n".encode())

    with _service() as (server, url):
        for path, message in invalid_files.values():
            result = _run(url, "voice-list", api_key_file=path)
            assert result.returncode != 0
            assert message in result.stderr
        assert server.records == []


def test_helper_sends_bearer_header_over_stdin_not_argv(tmp_path: Path) -> None:
    api_key = "secret-not-in-argv"
    api_key_file = tmp_path / "api-key"
    api_key_file.write_text(api_key + "\n", encoding="ascii")
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
        api_key_file=api_key_file,
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
