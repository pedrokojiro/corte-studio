@echo off
rem Corte Studio - Windows. Arquivo sem acentos de proposito (cmd le .bat no codepage OEM).
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

rem 1) Python: tenta "py" (launcher oficial) e depois "python".
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERRO] Python nao encontrado. Instale em https://www.python.org/downloads/ e marque "Add python.exe to PATH".
  goto fim
)

rem 2) ffmpeg / ffprobe no PATH.
where ffmpeg >nul 2>nul || (
  echo [ERRO] ffmpeg nao encontrado no PATH. Rode: winget install Gyan.FFmpeg  e abra este arquivo de novo.
  goto fim
)
where ffprobe >nul 2>nul || (
  echo [ERRO] ffprobe nao encontrado no PATH. Reinstale o ffmpeg: winget install Gyan.FFmpeg
  goto fim
)

rem 3) Dependencias (sem internet, segue com o que ja estiver instalado).
%PY% -m pip install -q -r requirements.txt || echo [AVISO] pip falhou. Seguindo com o que ja esta instalado.
%PY% -c "import flask" >nul 2>nul || (
  echo [ERRO] Flask nao instalado. Rode: %PY% -m pip install -r requirements.txt
  goto fim
)

rem 4) Abre o navegador 3s depois e sobe o servidor.
start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:5000"
%PY% app.py

:fim
echo.
pause
