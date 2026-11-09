@echo off
cd /d "%~dp0"
py -3 start_gpt.py
if errorlevel 1 pause
