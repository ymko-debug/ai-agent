@echo off
cd /d C:\Users\rusla\OneDrive\Documents\Agent
call venv\Scripts\activate
echo Starting AI Assistant on http://localhost:8000
echo Press Ctrl+C to stop.
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8000"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
