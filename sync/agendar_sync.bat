@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title Clube Diana - agendar sincronizacao
echo ============================================
echo   Agendar a sincronizacao automatica
echo   - cadastros novos: a cada 1 minuto (rapido, para o caixa)
echo   - compras e economia: a cada 15 minutos
echo ============================================
echo.
schtasks /create /tn "Clube Diana - Sync SIM" /sc minute /mo 15 /f /tr "\"C:\Windows\py.exe\" -3 \"%CD%\sync\clube_sync.py\" --all"
if errorlevel 1 (
  echo.
  echo NAO foi possivel agendar a sincronizacao completa.
  echo Rode este arquivo como administrador ou avise o suporte.
) else (
  echo.
  echo Sincronizacao completa agendada (15 min).
)
echo.
for /f "delims=" %%i in ('where pythonw') do set PYW=%%i
if not defined PYW set PYW=C:\Windows\py.exe
schtasks /create /tn "Clube Diana - Cadastros" /sc minute /mo 1 /f /tr "\"%PYW%\" \"%CD%\sync\clube_sync.py\" --push-cadastros"
if errorlevel 1 (
  echo NAO foi possivel agendar o envio de cadastros (1 min).
) else (
  echo Envio de cadastros agendado (1 min).
)
echo.
schtasks /query /tn "Clube Diana - Sync SIM" /fo LIST
schtasks /query /tn "Clube Diana - Cadastros" /fo LIST
pause >nul
