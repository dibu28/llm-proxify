import json

import httpx
import pytest
from openai import AuthenticationError, BadRequestError, OpenAI


def test_balancing_raw_body_headers_and_usage(make_proxy, chat, completion):
    raw = json.dumps({**chat, "temperature": 0.4, "vendor_extension": {"x": True}}).encode()
    with make_proxy() as (client, calls):
        for _ in range(4):
            response = client.post(
                "/v1/chat/completions?custom=true",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer local-placeholder",
                },
            )
            assert response.json() == completion
            assert response.headers["x-request-id"]
        assert [r.url.host for r in calls] == ["first", "second", "first", "second"]
        assert all(r.content == raw for r in calls)
        assert all(r.url.query == b"custom=true" for r in calls)
        assert all("authorization" not in r.headers for r in calls)
        assert client.get("/health").json()["backends"][0]["successes"] == 2


@pytest.mark.parametrize(
    "authorization", [None, "", "Bearer wrong", "Basic good", "Bearer good extra"]
)
def test_reject_invalid_tokens_before_forward(make_proxy, chat, authorization):
    with make_proxy(api_keys=("good",)) as (client, calls):
        headers = {"Authorization": authorization} if authorization is not None else {}
        response = client.post("/v1/chat/completions", json=chat, headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert set(response.json()["error"]) == {"message", "type", "param", "code"}
        assert not calls
        assert client.get("/health").status_code == 401
        assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("key", ["old-key", "new-key"])
def test_key_rotation_and_separate_upstream_key(make_proxy, chat, key):
    with make_proxy(api_keys=("old-key", "new-key"), backend_api_key="upstream-secret") as (
        client,
        calls,
    ):
        assert (
            client.post(
                "/v1/chat/completions", json=chat, headers={"Authorization": "bearer " + key}
            ).status_code
            == 200
        )
        assert calls[0].headers["authorization"] == "Bearer upstream-secret"


def test_duplicate_auth_headers_rejected(make_proxy, chat):
    with make_proxy(api_keys=("good",)) as (client, calls):
        response = client.post(
            "/v1/chat/completions",
            json=chat,
            headers=[("Authorization", "Bearer good"), ("Authorization", "Bearer bad")],
        )
        assert response.status_code == 401
        assert not calls


def test_explicit_auth_forwarding(make_proxy, chat):
    with make_proxy(forward_client_auth=True) as (client, calls):
        client.post("/v1/chat/completions", json=chat, headers={"Authorization": "Bearer upstream"})
        assert calls[0].headers["authorization"] == "Bearer upstream"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_preserved_without_retry(make_proxy, chat, status):
    body = (
        b'{"error":{"message":"bad input","type":"invalid_request_error",'
        b'"param":"model","code":null}}'
    )
    with make_proxy(
        lambda r: httpx.Response(
            status,
            content=body,
            headers={"Content-Type": "application/json", "x-request-id": "upstream-id"},
        )
    ) as (client, calls):
        response = client.post("/v1/chat/completions", json=chat)
        assert response.status_code == status
        assert response.content == body
        assert response.headers["x-request-id"] == "upstream-id"
        assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_failover_and_cooldown(make_proxy, chat, completion, status):
    def respond(request):
        return (
            httpx.Response(status, json={"error": {"message": "unavailable"}})
            if request.url.host == "first"
            else httpx.Response(200, json=completion)
        )

    with make_proxy(respond) as (client, calls):
        assert client.post("/v1/chat/completions", json=chat).status_code == 200
        assert client.post("/v1/chat/completions", json=chat).status_code == 200
        assert [r.url.host for r in calls] == ["first", "second", "second"]


def test_last_upstream_error_and_retry_headers_preserved(make_proxy, chat):
    with make_proxy(
        lambda r: httpx.Response(
            429,
            json={"error": {"message": "busy"}},
            headers={"retry-after": "12", "x-ratelimit-remaining-requests": "0"},
        )
    ) as (client, calls):
        response = client.post("/v1/chat/completions", json=chat)
        assert response.status_code == 429
        assert response.json() == {"error": {"message": "busy"}}
        assert response.headers["retry-after"] == "12"
        assert len(calls) == 2


def test_network_failures_and_model_discovery_do_not_fake_success(make_proxy, chat):
    def broken(request):
        raise httpx.ConnectError("secret URL must not reach the client", request=request)

    with make_proxy(broken, cooldown_sec=0) as (client, calls):
        for path in ("/v1/models", "/v1/chat/completions"):
            response = client.get(path) if path.endswith("models") else client.post(path, json=chat)
            assert response.status_code == 502
            assert response.json()["error"]["code"] == "backend_unavailable"
            assert "secret" not in response.text


def test_sse_bytes_and_safe_headers_preserved(make_proxy, chat, sse):
    with make_proxy(
        lambda r: httpx.Response(
            200,
            content=sse,
            headers={
                "Content-Type": "text/event-stream",
                "Set-Cookie": "secret=1",
                "Authorization": "Bearer secret",
            },
        )
    ) as (client, calls):
        response = client.post("/v1/chat/completions", json={**chat, "stream": True})
        assert response.content == sse
        assert response.headers["content-type"] == "text/event-stream"
        assert response.headers["x-accel-buffering"] == "no"
        assert "set-cookie" not in response.headers
        assert "authorization" not in response.headers
        assert client.get("/health").json()["backends"][0]["inflight"] == 0


def test_sdk_strict_json_stream_usage_and_models(make_proxy, chat, completion, sse):
    def respond(request):
        if request.url.path.startswith("/v1/models"):
            model = {
                "id": "test-model",
                "object": "model",
                "created": 1720000000,
                "owned_by": "local",
            }
            return httpx.Response(
                200,
                json={"object": "list", "data": [model]}
                if request.url.path == "/v1/models"
                else model,
            )
        if json.loads(request.content).get("stream"):
            return httpx.Response(200, content=sse, headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json=completion)

    with make_proxy(respond, api_keys=("sdk-key",)) as (client, calls):
        sdk = OpenAI(
            base_url="http://testserver/v1",
            api_key="sdk-key",
            http_client=client,
            max_retries=0,
            _strict_response_validation=True,
        )
        result = sdk.chat.completions.create(**chat)
        assert result.choices[0].message.content == "Hello"
        assert result.usage.total_tokens == 4
        stream = list(
            sdk.chat.completions.create(**chat, stream=True, stream_options={"include_usage": True})
        )
        assert stream[0].choices[0].delta.content == "Hello"
        assert stream[-1].usage.total_tokens == 4
        assert sdk.models.list().data[0].id == "test-model"
        assert sdk.models.retrieve("test-model").id == "test-model"


def test_sdk_auth_error_type(make_proxy, chat):
    with make_proxy(api_keys=("good",)) as (client, _):
        sdk = OpenAI(
            base_url="http://testserver/v1", api_key="bad", http_client=client, max_retries=0
        )
        with pytest.raises(AuthenticationError):
            sdk.chat.completions.create(**chat)


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"[]",
        b'{"model":"x","messages":[],"stream":false}',
        b'{"model":"x","messages":[{}],"stream":"yes"}',
        b'{"model":"x","messages":[{}],"temperature":NaN}',
        b'{"messages":[{}]}',
    ],
)
def test_invalid_json_and_required_parameters(make_proxy, body):
    with make_proxy() as (client, calls):
        response = client.post(
            "/v1/chat/completions", content=body, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["type"] == "invalid_request_error"
        assert not calls


def test_size_media_type_methods_routes_redirect(make_proxy, chat):
    with make_proxy(max_body_bytes=16) as (client, calls):
        assert client.post("/v1/chat/completions", json=chat).status_code == 413
        assert client.post("/v1/chat/completions", content=b"x").status_code == 415
        response = client.get("/v1/chat/completions")
        assert response.status_code == 405
        assert response.headers["allow"] == "POST"
        assert client.get("/v1/responses").status_code == 404
        assert not calls


def test_redirect_is_not_followed(make_proxy, chat):
    with make_proxy(lambda r: httpx.Response(307, headers={"Location": "http://untrusted/v1"})) as (
        client,
        calls,
    ):
        response = client.post("/v1/chat/completions", json=chat)
        assert response.status_code == 307
        assert len(calls) == 1


def test_sdk_invalid_request_error(make_proxy):
    with make_proxy() as (client, _):
        sdk = OpenAI(
            base_url="http://testserver/v1", api_key="local", http_client=client, max_retries=0
        )
        with pytest.raises(BadRequestError):
            sdk.chat.completions.create(model="", messages=[])
