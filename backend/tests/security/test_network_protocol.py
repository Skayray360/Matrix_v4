# Creado por Aldo Garcia.
"""Paquetes HTTP reales contra procesos Uvicorn propios, solo en loopback."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.security
BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", params=["h11", "httptools"])
def listener(request, tmp_path_factory):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = os.environ | {
        "APP_ENV": "test", "APP_HOST": "127.0.0.1", "APP_BASE_URL": f"http://127.0.0.1:{port}",
        "AUTH_PROVIDER": "local_test", "LOCAL_TEST_AUTH_ENABLED": "true",
        "HTTP_BODY_TIMEOUT_SECONDS": "0.2", "HTTP_BODY_MAX_BYTES": "1024", "PYTHONDONTWRITEBYTECODE": "1",
    }
    log = tmp_path_factory.mktemp("http-protocol") / "server.log"
    with log.open("w") as output:
        process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
            "--lifespan", "off", "--no-proxy-headers", "--http", request.param, "--log-level", "critical",
        ], cwd=BACKEND, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    pytest.fail("Uvicorn no inicio: " + log.read_text()[-1500:])
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        pytest.fail("El proceso de pruebas no abrio su puerto loopback")
                    time.sleep(0.05)
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def exchange(port: int, packet: bytes) -> tuple[int, bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        connection.sendall(packet)
        received = b""
        while b"\r\n\r\n" not in received:
            part = connection.recv(4096)
            assert part, "La conexion termino sin respuesta HTTP"
            received += part
        return int(received.split(b" ", 2)[1]), received


@pytest.mark.parametrize("framing", [
    b"Content-Length: 0\r\nContent-Length: 20\r\n",
    b"Content-Length: 0\r\nTransfer-Encoding: chunked\r\n",
    b"Content-Length: -1\r\n",
])
def test_ambiguous_framing_does_not_smuggle_a_second_request(listener, framing):
    packet = (b"POST /api/v1/auth/local/login HTTP/1.1\r\nHost: 127.0.0.1\r\n" + framing
              + b"Connection: close\r\n\r\n0\r\n\r\nGET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    status, _ = exchange(listener, packet)
    assert status == 400


def test_slow_request_body_releases_connection_with_timeout(listener):
    status, _ = exchange(listener, b"POST /api/v1/auth/local/login HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                         b"Content-Type: application/json\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{")
    assert status == 408


def test_chunked_upload_cannot_bypass_json_limit(listener):
    body = b"x" * 2048
    packet = (b"POST /api/v1/auth/local/login HTTP/1.1\r\nHost: 127.0.0.1\r\n"
              b"Transfer-Encoding: chunked\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n"
              + b"800\r\n" + body + b"\r\n0\r\n\r\n")
    status, _ = exchange(listener, packet)
    assert status == 413


def test_oversized_headers_are_bounded_with_both_http_parsers(listener):
    packet = (b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Padding: " + b"a" * 20000
              + b"\r\nConnection: close\r\n\r\n")
    status, _ = exchange(listener, packet)
    assert status in {400, 431}


def test_health_remains_available_after_malformed_traffic(listener):
    status, response = exchange(listener, b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
    assert status == 200
    assert b"x-content-type-options: nosniff" in response.lower()
