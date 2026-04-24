@echo off
echo Iniciando servidor de Control Biblioteca...
cd /d "%~dp0server"
call ..\venv\Scripts\activate 2>/dev/null || call ..\.venv\Scripts\activate 2>/dev/null
python -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
