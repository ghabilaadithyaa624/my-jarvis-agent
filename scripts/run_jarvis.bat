@echo off
setlocal
cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m jarvis %*
) else (
    echo [INFO] Virtual environment .venv not found. Using system Python...
    python -m jarvis %*
)
endlocal
