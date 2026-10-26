@echo off
rem SM64 headless for AIs: unlock the game with your own ROM, then plug the MCP server into your AI.
setlocal
cd /d "%~dp0"
set PY=
where py >nul 2>nul && (py -3 -c "import sys; assert sys.version_info >= (3, 9) and sys.maxsize > 2**32" >nul 2>nul && set PY=py -3)
if not defined PY (
  where python >nul 2>nul && (python -c "import sys; assert sys.version_info >= (3, 9) and sys.maxsize > 2**32" >nul 2>nul && set PY=python)
)
if not defined PY (
  echo Python 3.9 or newer, 64-bit, is needed: https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
  pause
  exit /b 1
)
%PY% install.py %*
pause
