@echo off
rem Starts the Ana w Wlidi control panel. Keep this window open while you use it.
cd /d "%~dp0..\.."
echo Panel starting... open http://localhost:8765
"venv\Scripts\python.exe" publisher\panel\app.py
pause
