"""Exercise the API through real HTTP without an additional HTTP client dependency."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest


@pytest.fixture(scope="module", params=[None, "not-a-url-with-synthetic-secret", "postgresql+psycopg://test:synthetic-secret@127.0.0.1:1/test_unavailable"])
def api_url(request):
    environment = os.environ.copy()
    for key in ("DATABASE_URL", "POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_PORT"):
        environment.pop(key, None)
    if request.param is not None:
        environment["DATABASE_URL"] = request.param
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f"API exited during startup: {process.stderr.read()}")
            try:
                with urlopen(f"{base_url}/openapi.json", timeout=0.3):
                    break
            except (URLError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail("API did not start within 10 seconds")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        process.stderr.close()


def test_health(api_url):
    with urlopen(f"{api_url}/health", timeout=2) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == "application/json"
        assert json.load(response) == {"status": "ok"}


def test_unknown_route(api_url):
    with pytest.raises(HTTPError) as error:
        urlopen(f"{api_url}/not-a-route", timeout=2)
    assert error.value.code == 404
    assert error.value.headers.get_content_type() == "application/json"
    assert json.load(error.value) == {"detail": "Not Found"}
    error.value.close()


def test_query_database_unavailable_is_safe(api_url):
    with pytest.raises(HTTPError) as error:
        urlopen(f"{api_url}/purchase-orders/PO-2026-0102", timeout=3)
    assert error.value.code == 503
    assert error.value.headers.get_content_type() == "application/json"
    assert json.load(error.value) == {"detail": "Database unavailable"}
    error.value.close()
