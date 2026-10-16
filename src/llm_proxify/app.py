"""ASGI proxy. Upstream JSON and SSE bytes are forwarded without rewriting."""

import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress
from urllib.parse import quote

import anyio
import httpx
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .config import Settings
from .pool import BackendPool, QueueFull

logger = logging.getLogger("llm_proxify")
logger.addHandler(logging.NullHandler())


def api_error(
    status: int,
    message: str,
    error_type: str,
    code: str,
    *,
    param: str | None = None,
    headers: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": error_type, "param": param, "code": code}},
        status_code=status,
        headers=headers,
    )


class AuthMiddleware:
    def __init__(self, app, keys: tuple[str, ...]):
        self.app = app
        self.keys = keys

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/healthz" and self.keys:
            headers = [value for name, value in scope["headers"] if name == b"authorization"]
            valid = False
            if len(headers) == 1:
                scheme, _, token = headers[0].partition(b" ")
                # Check every configured key; never log the supplied token.
                matches = [hmac.compare_digest(token, key.encode("ascii")) for key in self.keys]
                valid = scheme.lower() == b"bearer" and any(matches)
            if not valid:
                response = api_error(
                    401,
                    "Missing or invalid API key",
                    "invalid_request_error",
                    "invalid_api_key",
                    headers={"WWW-Authenticate": "Bearer"},
                )
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


def response_headers(response: httpx.Response, request_id: str) -> dict[str, str]:
    # Hop-by-hop headers, compressed Content-Length, cookies and credentials stay upstream.
    headers = {
        name: value
        for name, value in response.headers.items()
        if name in {"content-type", "retry-after", "x-request-id"}
        or name.startswith("x-ratelimit-")
    }
    headers.setdefault("x-request-id", request_id)
    if "text/event-stream" in headers.get("content-type", ""):
        headers.update({"cache-control": "no-cache", "x-accel-buffering": "no"})
    return headers


class UpstreamResponse(StreamingResponse):
    """Hold the backend reservation until streaming ends, including disconnects."""

    def __init__(self, upstream, iterator, first, pool, index, deadline, request_id):
        self.upstream = upstream
        self.pool = pool
        self.index = index
        self.deadline = deadline
        self.request_id = request_id
        self.failed = False

        async def chunks():
            if first:
                yield first
            async for chunk in iterator:
                yield chunk

        super().__init__(
            chunks(),
            status_code=upstream.status_code,
            headers=response_headers(upstream, request_id),
        )

    async def __call__(self, scope, receive, send):
        try:
            with anyio.fail_after(max(0, self.deadline - time.monotonic())):
                await super().__call__(scope, receive, send)
        except (TimeoutError, httpx.HTTPError):
            self.failed = True
            logger.warning("stream_aborted request_id=%s backend=%d", self.request_id, self.index)
            # Headers may already be sent. Abort rather than inventing a successful SSE ending.
            raise
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self.upstream.aclose()
                finally:
                    await self.pool.release(
                        self.index, self.upstream.status_code, failed=self.failed
                    )
            logger.info(
                "request_finished request_id=%s backend=%d status=%d",
                self.request_id,
                self.index,
                self.upstream.status_code,
            )


async def read_body(request: Request, settings: Settings, deadline: float):
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        return api_error(
            415,
            "Content-Type must be application/json",
            "invalid_request_error",
            "unsupported_media_type",
        )
    body = bytearray()
    with anyio.fail_after(max(0, deadline - time.monotonic())):
        async for chunk in request.stream():
            if len(body) + len(chunk) > settings.max_body_bytes:
                return api_error(
                    413, "Request body is too large", "invalid_request_error", "request_too_large"
                )
            body.extend(chunk)
    try:

        def invalid_constant(value):
            raise ValueError("Invalid JSON constant")

        obj = json.loads(body, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        return api_error(400, "Body must be valid JSON", "invalid_request_error", "invalid_json")
    if not isinstance(obj, dict):
        return api_error(400, "Body must be a JSON object", "invalid_request_error", "invalid_json")
    for name, valid in (
        ("model", isinstance(obj.get("model"), str) and bool(obj["model"].strip())),
        ("messages", isinstance(obj.get("messages"), list) and bool(obj["messages"])),
        ("stream", "stream" not in obj or isinstance(obj["stream"], bool)),
    ):
        if not valid:
            return api_error(
                400,
                f"Invalid or missing {name}",
                "invalid_request_error",
                "invalid_parameter",
                param=name,
            )
    # Optional model-specific fields and extensions are validated by the upstream.
    return bytes(body)


async def forward(request: Request):
    settings = request.app.state.settings
    pool = request.app.state.pool
    request_id = uuid.uuid4().hex
    deadline = time.monotonic() + settings.total_timeout_sec
    headers = {"accept-encoding": "identity"}
    if request.method == "POST":
        try:
            body = await read_body(request, settings, deadline)
        except TimeoutError:
            return api_error(
                408, "Timed out reading request", "invalid_request_error", "request_timeout"
            )
        except ClientDisconnect:
            return api_error(
                400, "Client disconnected", "invalid_request_error", "client_disconnect"
            )
        if isinstance(body, JSONResponse):
            return body
        headers["content-type"] = "application/json"
    else:
        body = b""
    if settings.backend_api_key:
        headers["authorization"] = "Bearer " + settings.backend_api_key
    elif settings.forward_client_auth and request.headers.get("authorization"):
        headers["authorization"] = request.headers["authorization"]
    for name in ("accept", "x-client-request-id", "openai-organization", "openai-project"):
        if name in request.headers:
            headers[name] = request.headers[name]
    try:
        # Preserve escaped model IDs; decoded '?' or '#' must not turn into URL delimiters.
        raw_path = request.scope.get("raw_path", quote(request.scope["path"], safe="/").encode())
        suffix = raw_path.decode("ascii")[3:]
        query = request.scope["query_string"].decode("ascii")
    except UnicodeDecodeError:
        return api_error(400, "URL must be percent-encoded", "invalid_request_error", "invalid_url")
    if query:
        suffix += "?" + query
    attempted = set()
    while True:
        try:
            index = await pool.acquire(attempted, deadline)
        except QueueFull:
            return api_error(
                429,
                "Proxy request queue is full",
                "rate_limit_error",
                "proxy_overloaded",
                headers={"retry-after": "1"},
            )
        except TimeoutError:
            return api_error(
                504, "Proxy request deadline exceeded", "server_error", "deadline_exceeded"
            )
        if index is None:
            return api_error(
                502,
                "All backends are unavailable",
                "server_error",
                "backend_unavailable",
                headers={"x-request-id": request_id},
            )
        attempted.add(index)
        upstream = None
        handed_off = False
        failed = False
        try:
            with anyio.fail_after(max(0, deadline - time.monotonic())):
                outgoing = request.app.state.client.build_request(
                    request.method,
                    pool.backends[index].url + suffix,
                    content=body,
                    headers=headers,
                    timeout=httpx.Timeout(settings.timeout_sec),
                )
                outgoing.headers.pop("cookie", None)
                upstream = await request.app.state.client.send(outgoing, stream=True)
                retryable = upstream.status_code == 429 or upstream.status_code >= 500
                if retryable and len(attempted) < len(pool.backends):
                    logger.warning(
                        "backend_retry request_id=%s backend=%d status=%d",
                        request_id,
                        index,
                        upstream.status_code,
                    )
                    continue
                iterator = upstream.aiter_bytes()
                first = await anext(iterator, b"")
            response = UpstreamResponse(
                upstream, iterator, first, pool, index, deadline, request_id
            )
            handed_off = True
            return response
        except (httpx.HTTPError, TimeoutError):
            failed = True
            logger.warning("backend_unavailable request_id=%s backend=%d", request_id, index)
        finally:
            if not handed_off:
                with anyio.CancelScope(shield=True):
                    try:
                        if upstream is not None:
                            await upstream.aclose()
                    finally:
                        await pool.release(
                            index, upstream.status_code if upstream else None, failed=failed
                        )


async def health(request: Request):
    if request.url.path == "/healthz":
        return JSONResponse({"status": "ok"})
    return JSONResponse(request.app.state.pool.snapshot())


async def http_error(request: Request, exc: HTTPException):
    message = "Method not allowed" if exc.status_code == 405 else "Unsupported endpoint"
    return api_error(
        exc.status_code,
        message,
        "invalid_request_error",
        "method_not_allowed" if exc.status_code == 405 else "not_found",
        headers=exc.headers,
    )


async def internal_error(request: Request, exc: Exception):
    logger.error("internal_error")
    return api_error(500, "Internal proxy error", "server_error", "internal_error")


def create_app(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> Starlette:
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings
        app.state.pool = BackendPool(settings)
        # Ignore ambient HTTP_PROXY variables; credentials go only to configured backends.
        async with httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=None),
        ) as client:
            app.state.client = client

            async def summaries():
                while True:
                    await asyncio.sleep(settings.summary_every_sec)
                    logger.info("summary %s", json.dumps(app.state.pool.snapshot()))

            task = None
            if settings.summary_every_sec > 0 and settings.log_level in {"info", "debug"}:
                task = asyncio.create_task(summaries())
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    return Starlette(
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Route("/v1/models", forward, methods=["GET"]),
            Route("/v1/models/{model}", forward, methods=["GET"]),
            Route("/v1/chat/completions", forward, methods=["POST"]),
        ],
        middleware=[Middleware(AuthMiddleware, keys=settings.api_keys)],
        exception_handlers={HTTPException: http_error, Exception: internal_error},
        lifespan=lifespan,
    )
