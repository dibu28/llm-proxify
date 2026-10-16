"""Portable CLI and console-only, opt-in standard logging."""

import argparse
import logging.config
import os
import sys

import uvicorn

from . import __version__
from .app import create_app
from .config import Settings, read_secret


def configure_logging(level: str):
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "console": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "console"},
                "null": {"class": "logging.NullHandler"},
            },
            "loggers": {
                "llm_proxify": {
                    "handlers": ["null" if level == "off" else "console"],
                    "level": "DEBUG" if level == "off" else level.upper(),
                    "propagate": False,
                },
                # Framework tracebacks may contain upstream URLs. Keep them out of console logs.
                "uvicorn": {"handlers": ["null"], "propagate": False},
                "uvicorn.error": {"handlers": ["null"], "propagate": False},
                "uvicorn.access": {"handlers": ["null"], "propagate": False},
                "httpx": {"handlers": ["null"], "propagate": False},
                "httpcore": {"handlers": ["null"], "propagate": False},
            },
        }
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Streaming proxy for OpenAI-compatible LLM servers"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--backends",
        default=os.getenv("LLAMA_BACKENDS", ""),
        help="Comma-separated backend URLs (or LLAMA_BACKENDS)",
    )
    parser.add_argument("--backend", action="append", default=[], help="Repeatable backend URL")
    parser.add_argument("--bind-host", default=os.getenv("PROXY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=os.getenv("PROXY_PORT", "8090"))
    parser.add_argument("--timeout-sec", type=float, default=os.getenv("PROXY_TIMEOUT_SEC", "90"))
    parser.add_argument(
        "--total-timeout-sec", type=float, default=os.getenv("PROXY_TOTAL_TIMEOUT_SEC", "150")
    )
    parser.add_argument("--cooldown-sec", type=float, default=os.getenv("PROXY_COOLDOWN_SEC", "30"))
    parser.add_argument(
        "--max-inflight-per-backend",
        type=int,
        default=os.getenv("PROXY_MAX_INFLIGHT_PER_BACKEND", "1"),
    )
    parser.add_argument("--max-queue", type=int, default=os.getenv("PROXY_MAX_QUEUE", "128"))
    parser.add_argument(
        "--max-body-bytes",
        type=int,
        default=os.getenv("PROXY_MAX_BODY_BYTES", str(10 * 1024 * 1024)),
    )
    parser.add_argument(
        "--log-level",
        choices=["off", "error", "warning", "info", "debug"],
        default=os.getenv("PROXY_LOG_LEVEL", "off"),
    )
    parser.add_argument("--verbose", action="store_true", help="Alias for --log-level debug")
    parser.add_argument(
        "--summary-every-sec", type=float, default=os.getenv("PROXY_SUMMARY_EVERY_SEC", "0")
    )
    parser.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="Allow a non-loopback bind without an API key (trusted networks only)",
    )
    parser.add_argument(
        "--forward-client-auth",
        action="store_true",
        help="Explicitly forward client Authorization to backends",
    )
    args = parser.parse_args(argv)
    try:
        keys = read_secret("PROXY_API_KEYS")
        return Settings(
            backends=tuple(
                args.backend + [b.strip() for b in args.backends.split(",") if b.strip()]
            ),
            host=args.bind_host,
            port=args.port,
            api_keys=tuple(key.strip() for key in keys.split(",")) if keys else (),
            backend_api_key=read_secret("BACKEND_API_KEY"),
            forward_client_auth=args.forward_client_auth,
            allow_unauthenticated=args.allow_unauthenticated,
            timeout_sec=args.timeout_sec,
            total_timeout_sec=args.total_timeout_sec,
            cooldown_sec=args.cooldown_sec,
            max_inflight_per_backend=args.max_inflight_per_backend,
            max_queue=args.max_queue,
            max_body_bytes=args.max_body_bytes,
            log_level="debug" if args.verbose else args.log_level,
            summary_every_sec=args.summary_every_sec,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


def main(argv=None):
    settings = parse_args(argv)
    configure_logging(settings.log_level)
    print(
        f"LLM Proxify {__version__} | port {settings.port} | "
        f"backends {len(settings.backends)} | auth {'on' if settings.api_keys else 'off'} | "
        f"logs {settings.log_level}",
        flush=True,
    )
    # One process: reservation limits are shared by all connections to this instance.
    try:
        uvicorn.run(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            log_config=None,
            access_log=False,
            proxy_headers=False,
            timeout_graceful_shutdown=10,
        )
    except SystemExit as exc:
        if exc.code:
            print("Server could not start. Check the bind address and port.", file=sys.stderr)
            return 1
    return 0
