@echo off
rem Abre o app sem deixar uma janela preta aberta.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo O app ainda nao foi instalado. De dois cliques em instalar.bat primeiro.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m transcrever_video
