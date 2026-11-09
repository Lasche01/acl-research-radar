@echo off
cd /d "%~dp0"
py -3 run.py serve
if errorlevel 1 (
  echo Python 3.10+ is required. See README.md.
  pause
)
