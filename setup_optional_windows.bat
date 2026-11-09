@echo off
cd /d "%~dp0"
py -3 -m pip install -r requirements-network.txt -r requirements-optional.txt
if errorlevel 1 (
  echo Optional support installation failed. Keep the error message.
) else (
  echo Certificate and PDF support installed. Restart ACL Research Radar.
)
pause
