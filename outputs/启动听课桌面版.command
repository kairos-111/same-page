#!/bin/zsh
APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$APP_ROOT/.venv/bin/python" "$APP_ROOT/scripts/desktop.py"
