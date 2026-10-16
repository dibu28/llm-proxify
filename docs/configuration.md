# Configuration

Command-line flags override their corresponding environment variables.
The Python CLI does not read `.env` automatically; Docker Compose does.
Use `llm-proxy --help` for all flags.

| Environment variable | Flag | Default |
| --- | --- | --- |
| `LLAMA_BACKENDS` | `--backends` or repeated `--backend` | Required |
| `PROXY_HOST` | `--bind-host` | `127.0.0.1` |
| `PROXY_PORT` | `--port` | `8090` |
| `PROXY_TIMEOUT_SEC` | `--timeout-sec` | `90` |
| `PROXY_TOTAL_TIMEOUT_SEC` | `--total-timeout-sec` | `150` |
| `PROXY_COOLDOWN_SEC` | `--cooldown-sec` | `30` |
| `PROXY_MAX_INFLIGHT_PER_BACKEND` | `--max-inflight-per-backend` | `1` |
| `PROXY_MAX_QUEUE` | `--max-queue` | `128` |
| `PROXY_MAX_BODY_BYTES` | `--max-body-bytes` | `10485760` (10 MiB) |
| `PROXY_LOG_LEVEL` | `--log-level` | `off` |
| `PROXY_SUMMARY_EVERY_SEC` | `--summary-every-sec` | `0` (disabled) |
| `PROXY_API_KEYS` | Environment only | No client authentication |
| `BACKEND_API_KEY` | Environment only | No upstream authentication |

Backend URLs can include `/v1` or omit it. A custom prefix such as
`https://example.com/api/v1` is supported. Duplicate URLs are removed.
URLs must not contain credentials, query strings or fragments.

`PROXY_API_KEYS` accepts comma-separated keys for key rotation; all listed keys are
valid for every supported route except public `/healthz`. `BACKEND_API_KEY` is a
separate single key used for all servers. Each secret also supports a file:
`PROXY_API_KEYS_FILE` or `BACKEND_API_KEY_FILE`. Do not set a value and its file
variable together. File contents are read once at startup.

Client Authorization is removed before contacting backends. To deliberately
forward it instead, use `--forward-client-auth`; this cannot be combined with
`BACKEND_API_KEY`. Incoming authentication remains independently controlled by
`PROXY_API_KEYS`.

## Logging

The CLI prints one startup line without secrets or backend addresses. Request,
access and periodic logs are off by default. `--log-level info` enables standard
Python console logging; `--verbose` is an alias for `--log-level debug`.
`--summary-every-sec 10 --log-level info` adds periodic queue and server counters.
No log files are created; redirect stderr or use `docker logs` if needed.

Logs contain generated request IDs, backend indices and statuses. They do not
contain prompts, bodies, Authorization, keys, query strings or upstream URLs.
Framework access/transport logs are disabled to prevent accidental exposure.
HTTP errors still reach clients when logging is off. Embedders of `create_app`
manage their own logging and must preserve these privacy rules.

## Container publishing

`.github/workflows/publish-container.yml` runs tests and container smoke checks,
then publishes Linux amd64/arm64 images to `ghcr.io/<owner>/<repository>` on `v*`
tags or manual dispatch. Release tags create version and `latest` tags; manual
runs create a SHA tag. No Docker Hub credentials are required: it uses the
repository's `GITHUB_TOKEN` with `packages: write`.

Create a GitHub repository, push this project, and push a tag such as `v0.2.0`.
After first publication, set the GHCR package visibility to **Public** if anonymous
pulls should work. Repository/organization policies may require enabling Actions
and package writes. The workflow is prepared locally; no remote repository or
container has been published yet.

[GitHub's container publishing documentation](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).
