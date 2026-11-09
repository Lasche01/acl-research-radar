@echo off
cd /d "%~dp0"
py -3 -m pip install -r requirements-network.txt
if errorlevel 1 (
  echo Network support installation failed. Keep the error message.
) else (
  echo Network support installed. Close and restart ACL Research Radar.
)
pause
