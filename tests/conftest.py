import inspect
import json
from contextlib import contextmanager

import httpx
import pytest
from starlette.testclient import TestClient

from llm_proxify.app import create_app
from llm_proxify.config import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def completion():
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1720000000,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello",
                    "refusal": None,
                },
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }


@pytest.fixture
def sse():
    events = [
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1720000000,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Hello"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1720000000,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1720000000,
            "model": "test-model",
            "choices": [],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        },
    ]
    return (
        b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events) + b"data: [DONE]\n\n"
    )


@pytest.fixture
def make_proxy(completion):
    @contextmanager
    def factory(responder=None, **kwargs):
        calls = []

        async def handle(request):
            calls.append(request)
            if responder:
                response = responder(request)
                return await response if inspect.isawaitable(response) else response
            return httpx.Response(200, json=completion)

        kwargs.setdefault("backends", ("http://first/v1", "http://second/v1"))
        app = create_app(Settings(**kwargs), transport=httpx.MockTransport(handle))
        with TestClient(app) as client:
            yield client, calls

    return factory


@pytest.fixture
def chat():
    return {"model": "test-model", "messages": [{"role": "user", "content": "Hello"}]}
