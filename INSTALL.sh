#!/usr/bin/env bash
# SM64 headless for AIs on Linux (INSTALL.bat is the Windows one): unlock the game with your own ROM, then plug the MCP
# server into your AI. The game DLLs run natively through headless/winload.c (built here once: a C compiler is needed).
#
#     ./INSTALL.sh [--rom-us ROM] [--rom-jp ROM] [--mupen EXE] [--no-register] [--yes]   (the options of install.py)
#
# A virtual environment .venv/ holds numpy and lz4 (a distribution's own Python refuses pip installs, PEP 668); the MCP
# server is registered with that Python.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; assert sys.version_info >= (3, 9) and sys.maxsize > 2**32' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 or newer, 64-bit, is needed (your distribution's python3 package)."
  exit 1
fi
if ! { command -v cc || command -v gcc || command -v clang; } >/dev/null 2>&1; then
  echo "A C compiler is needed (the loader of the game DLLs is built here): e.g. sudo pacman -S gcc / sudo apt install gcc"
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "making .venv (the headless game's Python packages)"
  "$PY" -m venv .venv
fi
exec .venv/bin/python install.py "$@"
