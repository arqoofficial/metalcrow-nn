.PHONY: sync test lint typecheck

sync:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy app
