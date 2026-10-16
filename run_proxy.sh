#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python -m llm_proxify "$@"
fi
exec python3 -m llm_proxify "$@"
