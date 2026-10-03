@echo off
rem Starts the mock client API (8099) and the decision lab (8000), each in its own
rem window, then opens the lab in the browser. Close the two windows to stop them.
cd /d "%~dp0"
start "mock API - 8099" .venv\Scripts\python.exe -m uvicorn mock_api.server:app --port 8099
start "decision lab - 8000" .venv\Scripts\python.exe -m uvicorn src.service:app --port 8000
timeout /t 5 /nobreak >nul
start "" http://127.0.0.1:8000
