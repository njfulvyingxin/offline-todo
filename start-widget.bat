@echo off
cd /d %~dp0
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw widget.py
  exit /b 0
)
where py >nul 2>nul
if not errorlevel 1 (
  start "" py -3w widget.py
  exit /b 0
)
echo Python 3 not found. Install it first: https://www.python.org/downloads/
pause
exit /b 1
