@echo off
rem Corte Studio - Windows. Arquivo sem acentos de proposito (cmd le .bat no codepage OEM).
rem Usa um ambiente virtual proprio (.venv): NAO instala nem atualiza nada no Python do sistema.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"

rem 1) ffmpeg / ffprobe no PATH.
where ffmpeg >nul 2>nul || (
  echo [ERRO] ffmpeg nao encontrado no PATH. Rode: winget install Gyan.FFmpeg  e abra este arquivo de novo.
  goto falha
)
where ffprobe >nul 2>nul || (
  echo [ERRO] ffprobe nao encontrado no PATH. Reinstale: winget install Gyan.FFmpeg
  goto falha
)

rem 2) Ambiente virtual .venv, criado uma vez com um Python 3.12 ou mais novo.
if exist "%VPY%" goto deps
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERRO] Python 3.12 ou mais novo nao encontrado. Instale em https://www.python.org/downloads/
  echo        e marque "Add python.exe to PATH".
  goto falha
)
echo Criando o ambiente do app em .venv (so na primeira vez)...
%PY% -m venv "%VENV%"
if errorlevel 1 (
  echo [ERRO] Nao consegui criar o .venv.
  goto falha
)

:deps
rem 3) Dependencias fixadas, so dentro do .venv. Reinstala apenas quando requirements/constraints mudam.
fc /b requirements.txt "%VENV%\requirements.ok" >nul 2>nul && fc /b constraints.txt "%VENV%\constraints.ok" >nul 2>nul && goto rodar
echo Instalando dependencias no .venv...
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt -c constraints.txt
if errorlevel 1 (
  echo [ERRO] Falha ao instalar as dependencias. Verifique a internet e abra este arquivo de novo.
  goto falha
)
copy /y requirements.txt "%VENV%\requirements.ok" >nul
copy /y constraints.txt "%VENV%\constraints.ok" >nul

:rodar
rem 4) Abre o navegador 3 s depois e sobe o servidor.
start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:5000"
"%VPY%" app.py
if errorlevel 1 goto falha
goto fim

:falha
echo.
echo O Corte Studio nao iniciou. Leia a mensagem acima.

:fim
echo.
pause
