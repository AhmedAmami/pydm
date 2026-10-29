@echo off
rem YouTube changes often; when YouTube downloads start failing, run this first.
cd /d "%~dp0"
.venv\Scripts\python -m pip install -U "yt-dlp[default]" deno
pause
