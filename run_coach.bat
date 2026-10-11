@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
.venv\Scripts\python.exe -m coach run >> logs\coach.log 2>&1
