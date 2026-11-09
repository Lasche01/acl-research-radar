@echo off
cd /d "%~dp0"
py -3 run.py diagnose --output source-diagnostics.json
pause
