# macOS / Linux (bash, sh) 前提。Windows では WSL または Git Bash を使う。

.PHONY: install sync lint types test docs pre-commit clean

install:
	curl -LsSf https://astral.sh/uv/install.sh | sh

sync:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

types:
	uv run mypy .

test:
	uv run pytest -q

docs:
	uv run mkdocs build --strict

pre-commit:
	uv run pre-commit run --all-files

clean:
	find . \( -type d -name "__pycache__" -o -type d -name ".pytest_cache" -o -type f -name "*.pyc" \) -exec rm -rf {} +
	rm -rf site .ruff_cache .mypy_cache
