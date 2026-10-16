# Security

Use token-free mode only on your own machine or a trusted private network.
The default bind is `127.0.0.1`. A non-loopback bind requires `PROXY_API_KEYS`,
unless `--allow-unauthenticated` is explicitly supplied for a trusted network.

For Internet access, put the proxy behind an **HTTPS reverse proxy** (for example,
Caddy or nginx), configure strong random API keys and restrict the upstream
servers with firewall rules. Bearer tokens over plain HTTP do not provide
confidentiality. The application does not terminate TLS. Disable buffering for SSE
in your reverse proxy and set its timeouts longer than the proxy's total deadline.

Authentication protects all routes except `/healthz`, which reveals only liveness.
Keys are compared with `hmac.compare_digest`. Client keys are never forwarded to
servers unless explicitly requested. Use `BACKEND_API_KEY` for upstream auth and
HTTPS backend URLs when those connections cross an untrusted network. TLS
certificate validation is enabled; there is no insecure-skip-verify option.

Keep `.env` and secret files out of Git. Environment variables can be inspected
by administrators; use `*_FILE` variables with mounted secret files where possible.
Rotate keys by adding a new key, updating clients, then removing the old key and
restarting the proxy. There is no per-user quota, billing, IP rate limit or full
DDoS protection. Bounded queues/bodies do not replace an Internet-facing gateway's
connection, bandwidth and rate limits. Upstream providers can include sensitive
details in their error bodies, which this transparent proxy passes to authenticated
clients. Configure trusted upstreams accordingly.

To report a vulnerability, use the repository's private GitHub vulnerability
reporting feature if enabled. Otherwise contact the repository owner privately;
do not include tokens or confidential prompts in a public issue.
