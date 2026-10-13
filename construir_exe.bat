@echo off
rem Gera dist\CorteStudio\CorteStudio.exe. Arquivo sem acentos de proposito (cmd le .bat no codepage OEM).
rem Usa o .venv do app (crie antes com iniciar.bat): nao instala nada no Python do sistema.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo [ERRO] .venv nao encontrado. Rode o iniciar.bat uma vez e depois este arquivo.
  goto fim
)
"%VPY%" construir_exe.py
if errorlevel 1 (
  echo [ERRO] O executavel nao foi gerado. Leia a mensagem acima.
  goto fim
)
echo.
echo Este arquivo so GERA o app; ele nao abre o app.
echo Para abrir: dist\CorteStudio\CorteStudio.exe (a pasta vai abrir agora com ele selecionado).
explorer /select,"%~dp0dist\CorteStudio\CorteStudio.exe"
:fim
echo.
pause
