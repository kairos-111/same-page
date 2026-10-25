#!/bin/bash
# Smoke-test the staged Windows build under Wine 10 (amd64 container). Read-only mount of the stage.
# Usage: windows/smoke_wine.sh [speech.wav]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WAV="${1:-$ROOT/tests/fixtures/hello_en.wav}"
docker rm -f samepage-smoke >/dev/null 2>&1 || true
docker run -d --name samepage-smoke --platform linux/amd64 -v "$ROOT/work/win/stage:/app:ro" \
  -v "$(dirname "$WAV"):/sample:ro" -v "$ROOT/windows:/win:ro" samepage-wine10 sleep infinity >/dev/null
trap 'docker rm -f samepage-smoke >/dev/null 2>&1 || true' EXIT
docker exec samepage-smoke bash -c 'wineboot -i >/dev/null 2>&1 || true'
docker exec samepage-smoke bash -c "wine 'Z:\\app\\engine\\work\\runtime\\python\\python.exe' -I -B 'Z:\\win\\smoke_inside.py' 'Z:\\sample\\$(basename "$WAV")'" 2>&1 \
  | grep -v '^fixme\|^err:\|^ *0000\|wine_\|^Unhandled\|winedbg'
