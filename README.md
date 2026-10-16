# LLM Proxify

A small load balancer for llama.cpp and other OpenAI-compatible LLM servers.
Use one API address for several replicas, with live streaming, failover and a
per-server concurrency limit. Runs on **Windows, Linux and macOS** with Python 3.10+,
or in Docker. Licensed under MIT.

## Quick start

Download or clone this repository and open a terminal in its directory.

**Windows (PowerShell):**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\llm-proxy.exe --backends http://127.0.0.1:8088/v1,http://127.0.0.1:8089/v1
```

**Linux / macOS:**

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/llm-proxy --backends http://127.0.0.1:8088/v1,http://127.0.0.1:8089/v1
```

Replace the backend URLs with your running servers. They should serve the same
model ID. The client base URL is **`http://127.0.0.1:8090/v1`**. No proxy token is
required for this local setup. Nothing needs to be downloaded except Python
dependencies; models and model servers are managed separately.

Windows shortcuts: `setup.cmd`, then `run_proxy.cmd --backends ...`.
Linux/macOS shortcut after installation: `sh run_proxy.sh --backends ...`.
`python -m llm_proxify` is equivalent to `llm-proxy` in the installed environment.

## Connect a client

Set your application's OpenAI-compatible base URL to `http://127.0.0.1:8090/v1`
and use a model ID offered by your backends. If it requires a key field in local
token-free mode, enter any placeholder, such as `local`.

With the Python OpenAI SDK (`pip install openai`):

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8090/v1", api_key="local", max_retries=0)
result = client.chat.completions.create(
    model="your-model-id",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(result.choices[0].message.content)
```

`stream=True` enables live SSE. JSON, tool calls and model-specific parameters
are passed through. This release supports **Chat Completions and Models**, not
every OpenAI endpoint. See [API compatibility](docs/compatibility.md).

## Optional access tokens

Generate a token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Set it before starting the proxy:

```powershell
# Windows PowerShell
$env:PROXY_API_KEYS = "your-random-token"
```

```sh
# Linux / macOS
export PROXY_API_KEYS="your-random-token"
```

Clients now send `Authorization: Bearer your-random-token` (or set SDK `api_key`
to that token). Set `BACKEND_API_KEY` separately if your model servers require
authentication. The proxy never forwards its client token by default.

For Internet access, use an **HTTPS reverse proxy**, tokens and firewall rules.
To listen beyond localhost, add `--bind-host 0.0.0.0` with tokens configured.
See [security](SECURITY.md) for the deployment boundaries.

## Docker run

Copy `.env.example` to `.env`, set `PROXY_API_KEYS` to a random token, and edit
`LLAMA_BACKENDS`. Then:

```sh
docker build -t llm-proxify .
docker run --rm --name llm-proxy -p 127.0.0.1:8090:8090 --add-host=host.docker.internal:host-gateway --env-file .env llm-proxify
```

Use Linux containers in Docker Desktop on Windows/macOS. `host.docker.internal`
reaches the host; Linux uses the added host-gateway mapping. Your backend must
listen on an address reachable from the container, not only its own loopback.
Keep backend ports private with firewall rules. For servers in another container,
use their service names on a shared Docker network. A container's `127.0.0.1`
refers to that container.

## Docker Compose

Use the same `.env` from above:

```sh
docker compose up -d --build
docker compose logs -f
```

Stop with `docker compose down`. The example publishes the proxy port only on
the host's localhost and runs the container as a non-root user.

## Logs and settings

Logging is **off by default**, apart from one startup line. Enable console logs
with `--log-level info`; add `--summary-every-sec 10` for periodic counters.
Prompts and keys are not logged. No extra file logger is needed; Docker captures
console output automatically.

Use `llm-proxy --help` or see [configuration](docs/configuration.md).
`GET /healthz` checks liveness; `GET /health` shows queue and server counters.

## GitHub container publishing

The included workflow tests the project and publishes amd64/arm64 images to
**GitHub Container Registry (GHCR)** when you push a `v*` tag, such as `v0.2.0`.
It uses `GITHUB_TOKEN`; Docker Hub credentials are unnecessary. After publishing,
the image is `ghcr.io/<owner>/<repository>:0.2.0`. Set the package to Public for
anonymous pulls. See [publishing details](docs/configuration.md#container-publishing).

For development and tests, see [CONTRIBUTING.md](CONTRIBUTING.md).
