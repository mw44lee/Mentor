@echo off
rem If the app is already running (autostart), just open the browser; otherwise start it hidden first.
cd /d %~dp0
curl -s -o nul http://127.0.0.1:8770/login
if errorlevel 1 (
  start "" .venv\Scripts\pythonw.exe serve.py
  timeout /t 3 /nobreak > nul
)
start "" http://127.0.0.1:8770
