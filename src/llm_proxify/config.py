"""Configuration shared by the CLI and embedded ASGI applications."""

import ipaddress
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def normalize_backend(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Backend URLs must use http:// or https://")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("Backend URLs must not contain credentials, query strings or fragments")
    try:
        parts.port
    except ValueError as exc:
        raise ValueError("Invalid backend port") from exc
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def read_secret(name: str) -> str:
    value = os.environ.get(name, "")
    filename = os.environ.get(name + "_FILE", "")
    if value and filename:
        raise ValueError(f"Use either {name} or {name}_FILE, not both")
    if filename:
        value = Path(filename).read_text(encoding="utf-8").strip()
    return value


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    backends: tuple[str, ...]
    host: str = "127.0.0.1"
    port: int = 8090
    api_keys: tuple[str, ...] = field(default=(), repr=False)
    backend_api_key: str = field(default="", repr=False)
    forward_client_auth: bool = False
    allow_unauthenticated: bool = False
    timeout_sec: float = 90
    total_timeout_sec: float = 150
    cooldown_sec: float = 30
    max_inflight_per_backend: int = 1
    max_queue: int = 128
    max_body_bytes: int = 10 * 1024 * 1024
    log_level: str = "off"
    summary_every_sec: float = 0

    def __post_init__(self):
        normalized = tuple(dict.fromkeys(normalize_backend(b) for b in self.backends))
        if not normalized:
            raise ValueError("Provide --backends or LLAMA_BACKENDS")
        object.__setattr__(self, "backends", normalized)
        for name in ("timeout_sec", "total_timeout_sec"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("cooldown_sec", "summary_every_sec"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.max_inflight_per_backend < 1 or self.max_queue < 0 or self.max_body_bytes < 1:
            raise ValueError("Invalid capacity or body size limit")
        for key in (*self.api_keys, self.backend_api_key):
            if key and (not key.isascii() or any(c.isspace() for c in key)):
                raise ValueError("API keys must be ASCII strings without whitespace")
        if any(not key for key in self.api_keys):
            raise ValueError("API keys must not be empty")
        if self.forward_client_auth and self.backend_api_key:
            raise ValueError("Forwarded client auth and BACKEND_API_KEY are mutually exclusive")
        if self.log_level not in {"off", "error", "warning", "info", "debug"}:
            raise ValueError("Invalid log level")
        if not is_loopback(self.host) and not self.api_keys and not self.allow_unauthenticated:
            raise ValueError(
                "A public bind requires PROXY_API_KEYS. For a trusted private network only, "
                "use --allow-unauthenticated explicitly"
            )
