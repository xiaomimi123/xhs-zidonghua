@echo off
cd /d "%~dp0.."
.venv\Scripts\python collector\collector.py
pause
