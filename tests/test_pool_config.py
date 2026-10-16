import asyncio
import time

import pytest

from llm_proxify.cli import configure_logging, parse_args
from llm_proxify.config import Settings, normalize_backend
from llm_proxify.pool import BackendPool, QueueFull


@pytest.mark.anyio
async def test_atomic_capacity_bounded_queue_and_cancel_cleanup():
    pool = BackendPool(Settings(backends=("http://first", "http://second"), max_queue=1))
    deadline = time.monotonic() + 2
    first, second = await asyncio.gather(
        pool.acquire(set(), deadline), pool.acquire(set(), deadline)
    )
    assert first != second
    waiting = asyncio.create_task(pool.acquire(set(), deadline))
    for _ in range(100):
        if pool.waiting == 1:
            break
        await asyncio.sleep(0.005)
    assert pool.waiting == 1
    with pytest.raises(QueueFull):
        await pool.acquire(set(), deadline)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert pool.waiting == 0
    await pool.release(first, 200)
    assert await pool.acquire(set(), deadline) == first
    await pool.release(first, 200)
    await pool.release(second, 200)
    assert all(b.inflight == 0 for b in pool.backends)


@pytest.mark.anyio
async def test_waiter_wakes_on_release_and_deadline_is_enforced():
    pool = BackendPool(Settings(backends=("http://first",)))
    first = await pool.acquire(set(), time.monotonic() + 1)
    waiting = asyncio.create_task(pool.acquire(set(), time.monotonic() + 1))
    await asyncio.sleep(0.01)
    assert not waiting.done()
    await pool.release(first, 200)
    assert await waiting == first
    with pytest.raises(TimeoutError):
        await pool.acquire(set(), time.monotonic() + 0.02)
    assert pool.waiting == 0
    await pool.release(first, 200)
    with pytest.raises(TimeoutError):
        await pool.acquire(set(), time.monotonic() - 1)


@pytest.mark.parametrize(
    "url,normalized",
    [
        ("localhost:8088", "http://localhost:8088/v1"),
        ("https://example.com/api/v1/", "https://example.com/api/v1"),
        ("http://[::1]:8088", "http://[::1]:8088/v1"),
    ],
)
def test_backend_normalization(url, normalized):
    assert normalize_backend(url) == normalized


@pytest.mark.parametrize(
    "url",
    [
        "ftp://host",
        "http://user:secret@host",
        "http://host?key=x",
        "http://host/#x",
        "http://host:bad",
    ],
)
def test_unsafe_backend_urls_rejected(url):
    with pytest.raises(ValueError):
        normalize_backend(url)


@pytest.mark.parametrize(
    "options",
    [
        {"host": "0.0.0.0"},
        {"host": "::"},
        {"timeout_sec": 0},
        {"total_timeout_sec": float("nan")},
        {"cooldown_sec": -1},
        {"max_queue": -1},
        {"max_inflight_per_backend": 0},
        {"api_keys": ("",)},
        {"api_keys": ("bad key",)},
        {"backend_api_key": "не-ASCII"},
        {"forward_client_auth": True, "backend_api_key": "secret"},
    ],
)
def test_invalid_or_unsafe_settings(options):
    with pytest.raises(ValueError):
        Settings(backends=("http://localhost",), **options)


def test_public_bind_requires_explicit_auth_or_opt_out():
    assert Settings(backends=("http://localhost",), host="0.0.0.0", api_keys=("secret",))
    assert Settings(backends=("http://localhost",), host="0.0.0.0", allow_unauthenticated=True)
    settings = Settings(
        backends=("http://localhost",), api_keys=("secret",), backend_api_key="private"
    )
    assert "secret" not in repr(settings) and "private" not in repr(settings)


def test_cli_env_file_secrets_and_override(monkeypatch, tmp_path):
    path = tmp_path / "key.txt"
    path.write_text("file-key\n", encoding="utf-8")
    monkeypatch.setenv("PROXY_API_KEYS_FILE", str(path))
    monkeypatch.delenv("PROXY_API_KEYS", raising=False)
    monkeypatch.setenv("LLAMA_BACKENDS", "http://first")
    monkeypatch.setenv("PROXY_PORT", "8091")
    monkeypatch.delenv("BACKEND_API_KEY", raising=False)
    monkeypatch.delenv("BACKEND_API_KEY_FILE", raising=False)
    settings = parse_args(["--port", "8092"])
    assert settings.api_keys == ("file-key",)
    assert settings.port == 8092
    assert settings.log_level == "off" and settings.summary_every_sec == 0
    monkeypatch.setenv("PROXY_API_KEYS", "env-key")
    with pytest.raises(SystemExit):
        parse_args([])


def test_logging_default_silent_and_opt_in_redacted(make_proxy, chat, capsys):
    configure_logging("off")
    with make_proxy(api_keys=("do-not-log-me",)) as (client, _):
        client.post(
            "/v1/chat/completions", json=chat, headers={"Authorization": "Bearer do-not-log-me"}
        )
    assert capsys.readouterr().err == ""
    try:
        configure_logging("info")
        with make_proxy(api_keys=("do-not-log-me",)) as (client, _):
            client.post(
                "/v1/chat/completions?private=secret",
                json=chat,
                headers={"Authorization": "Bearer do-not-log-me"},
            )
        output = capsys.readouterr().err
        assert "request_finished" in output
        for private in ("do-not-log-me", "private=secret", "http://first", "Hello"):
            assert private not in output
    finally:
        configure_logging("off")
