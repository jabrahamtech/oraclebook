# Contributing

oraclebook is solo-maintained pre-alpha software. Contributions are welcome, but please keep
PRs small and focused — a large PR is hard to review carefully for a project this young.

## Dev setup

```bash
pip install -e .[dev]
```

This installs `pytest`, `pytest-asyncio`, and `ruff` alongside the package in editable mode.

## Before opening a PR

```bash
ruff check .
pytest
```

Both must pass. `asyncio_mode = "auto"` is set in `pyproject.toml`, so async tests don't need
`@pytest.mark.asyncio`.

## Code style

- Line length 100 (enforced by `ruff`, see `[tool.ruff]` in `pyproject.toml`).
- `structlog` for logging — never `print()`.
- Protocol-based dependency injection: strategies, gates, feeds, and executors depend on the
  protocols in `oraclebook/core/protocols.py`, not on each other's concrete classes. See
  `NAMING.md` for the vocabulary and `AGENTS.md` for extension recipes.
- No `hasattr` guards, no reaching into another class's private attributes.

## Pull requests

Small, focused PRs are much easier to merge than large ones. If you're planning something
substantial (a new venue adapter, a new strategy), consider opening an issue first to discuss
the approach.

No CLA is required.
