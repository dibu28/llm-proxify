import copy
import json

import httpx
from openai import OpenAI


def test_sdk_tool_calls_and_multimodal_request_are_unchanged(make_proxy, completion):
    payload = copy.deepcopy(completion)
    payload["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_test",
                "type": "function",
                "function": {"name": "weather", "arguments": '{"city":"Paris"}'},
            }
        ],
    }
    payload["choices"][0]["finish_reason"] = "tool_calls"
    with make_proxy(lambda r: httpx.Response(200, json=payload)) as (client, calls):
        sdk = OpenAI(
            base_url="http://testserver/v1",
            api_key="local",
            http_client=client,
            max_retries=0,
            _strict_response_validation=True,
        )
        result = sdk.chat.completions.create(
            model="test-model",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                    ],
                }
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        },
                    },
                }
            ],
            response_format={"type": "json_object"},
        )
        assert result.choices[0].message.tool_calls[0].function.name == "weather"
        forwarded = json.loads(calls[0].content)
        assert forwarded["messages"][0]["content"][1]["image_url"]["url"].startswith("data:")
        assert forwarded["tools"][0]["function"]["name"] == "weather"
        assert forwarded["response_format"] == {"type": "json_object"}


def test_backend_cookie_is_not_reused(make_proxy, chat):
    with make_proxy(
        lambda r: httpx.Response(
            200, json={"ok": True}, headers={"set-cookie": "session=secret; Path=/"}
        ),
        backends=("http://same-host:8088/v1", "http://same-host:8089/v1"),
    ) as (client, calls):
        for _ in range(2):
            assert client.post("/v1/chat/completions", json=chat).status_code == 200
        assert all("cookie" not in request.headers for request in calls)
