# Contributing

Python 3.10+ is required. From the repository directory, create and activate a
virtual environment, then run:

```sh
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
python -m pytest -q
python -m build
```

Tests use local mock servers, so no paid API or real API token is needed. CI runs
them on Windows, Linux and macOS and builds/smoke-tests the Linux container.
Add regression tests for changes to streaming, auth, error formats or reservations.
Keep credentials and prompts out of logs and fixtures. Submit a pull request with
the behavior changed and the checks run. Contributions are licensed under MIT.
