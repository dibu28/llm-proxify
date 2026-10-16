import asyncio
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import uvicorn

from llm_proxify.app import create_app
from llm_proxify.config import Settings


@contextmanager
def live_server(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_config=None, access_log=False, timeout_graceful_shutdown=1)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert server.started, "Local HTTP server failed to start"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


class GatedStream(httpx.AsyncByteStream):
    def __init__(self, gate, closed):
        self.gate = gate
        self.closed = closed

    async def __aiter__(self):
        yield b'data: {"delta":"first"}\n\n'
        while not self.gate.is_set():
            await asyncio.sleep(0.01)
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        self.closed.set()


def test_live_stream_not_buffered_and_disconnect_releases_slot(chat):
    gate, closed = threading.Event(), threading.Event()
    stream = GatedStream(gate, closed)
    app = create_app(
        Settings(backends=("http://first",), total_timeout_sec=5, max_queue=0),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, stream=stream
            )
        ),
    )
    try:
        with live_server(app) as url, httpx.Client(timeout=2) as client:
            with client.stream(
                "POST", url + "/v1/chat/completions", json={**chat, "stream": True}
            ) as response:
                lines = response.iter_lines()
                assert next(lines) == 'data: {"delta":"first"}'
                assert not gate.is_set()  # Upstream is still blocked; first event already arrived.
                assert client.get(url + "/health").json()["backends"][0]["inflight"] == 1
                busy = client.post(url + "/v1/chat/completions", json=chat)
                assert busy.status_code == 429
                assert busy.json()["error"]["code"] == "proxy_overloaded"
                assert client.get(url + "/v1/models").status_code == 429
            assert closed.wait(timeout=2)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if client.get(url + "/health").json()["backends"][0]["inflight"] == 0:
                    break
                time.sleep(0.01)
            assert client.get(url + "/health").json()["backends"][0]["inflight"] == 0
    finally:
        gate.set()


def test_stream_total_deadline_terminates_and_frees_slot(chat):
    gate, closed = threading.Event(), threading.Event()
    app = create_app(
        Settings(backends=("http://first",), total_timeout_sec=0.15),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, stream=GatedStream(gate, closed)
            )
        ),
    )
    try:
        with live_server(app) as url, httpx.Client(timeout=2) as client:
            started = time.monotonic()
            try:
                with client.stream(
                    "POST", url + "/v1/chat/completions", json={**chat, "stream": True}
                ) as response:
                    payload = b"".join(response.iter_bytes())
            except httpx.RemoteProtocolError:
                payload = b""
            assert b"[DONE]" not in payload
            assert time.monotonic() - started < 2
            assert closed.wait(timeout=2)
            assert client.get(url + "/health").json()["backends"][0]["inflight"] == 0
    finally:
        gate.set()


def test_deadline_before_headers_is_openai_json_error(chat):
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    app = create_app(
        Settings(backends=("http://first",), total_timeout_sec=0.05),
        transport=httpx.MockTransport(slow),
    )
    with live_server(app) as url, httpx.Client(timeout=2) as client:
        response = client.post(url + "/v1/chat/completions", json=chat)
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "deadline_exceeded"
        assert client.get(url + "/health").json()["backends"][0]["inflight"] == 0
