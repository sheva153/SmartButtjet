set dotenv-load
export UV_CACHE_DIR := ".uv-cache"

setup:
    python -m uv sync

run:
    python -m uv run python main.py bot

parse message:
    python -m uv run python main.py parse "{{message}}"

test:
    python -m uv run pytest

lint:
    python -m uv run ruff check .

format:
    python -m uv run ruff format .

typecheck:
    python -m uv run pyright

check:
    python -m uv run ruff format --check .
    python -m uv run ruff check .
    python -m uv run pyright
    python -m uv run pytest

check-config:
    python -m uv run python main.py check-config

check-storage:
    python -m uv run python main.py check-storage

analytics period="month":
    python -m uv run python main.py analytics --period "{{period}}"

export:
    python -m uv run python main.py export

tmux-setup:
    tmux source-file -n "$HOME/.tmux.conf"
    tmux source-file "$HOME/.tmux.conf"
    @echo "tmux configuration reloaded"
