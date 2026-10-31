@echo off
rem Instala o app: cria o ambiente virtual (.venv) e baixa as dependências do PyPI.
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python nao encontrado. Instale o Python 3.12 ou mais novo em https://www.python.org/downloads/
    echo e marque "Add python.exe to PATH" na instalacao.
    pause
    exit /b 1
)

py -3 -m venv .venv || goto erro
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto erro
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto erro

echo.
echo Pronto! Para abrir o app, de dois cliques em iniciar.bat
pause
exit /b 0

:erro
echo.
echo A instalacao falhou. Leia a mensagem acima.
pause
exit /b 1
