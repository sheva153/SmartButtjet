set dotenv-load
export UV_CACHE_DIR := ".uv-cache"

setup:
    uv sync

run:
    uv run python main.py bot

parse message:
    uv run python main.py parse "{{message}}"

test:
    uv run pytest

lint:
    uv run ruff check .

format:
    uv run ruff format .

typecheck:
    uv run pyright

check:
    uv run ruff format --check .
    uv run ruff check .
    uv run pyright
    uv run pytest

check-config:
    uv run python main.py check-config

check-storage:
    uv run python main.py check-storage

analytics period="month":
    uv run python main.py analytics --period "{{period}}"

export:
    uv run python main.py export

tmux-setup:
    tmux source-file -n "$HOME/.tmux.conf"
    tmux source-file "$HOME/.tmux.conf"
    @echo "tmux configuration reloaded"
