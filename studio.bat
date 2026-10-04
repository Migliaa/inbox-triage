@echo off
rem Starts the mock client API (8099) and the LangGraph dev server (2024), then opens
rem LangGraph Studio in the browser (it asks for a free LangSmith login).
rem Close the two windows to stop them.
cd /d "%~dp0"
start "mock API - 8099" .venv\Scripts\python.exe -m uvicorn mock_api.server:app --port 8099
start "LangGraph - 2024" .venv\Scripts\langgraph.exe dev --port 2024 --studio-url https://eu.smith.langchain.com
