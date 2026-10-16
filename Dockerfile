FROM python:3.13-slim

LABEL org.opencontainers.image.title="LLM Proxify" \
      org.opencontainers.image.description="Streaming load balancer for OpenAI-compatible LLM servers" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PROXY_HOST=0.0.0.0
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install . && \
    groupadd --gid 10001 llmproxify && \
    useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin llmproxify
USER 10001:10001
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).close()"
ENTRYPOINT ["python", "-m", "llm_proxify"]
