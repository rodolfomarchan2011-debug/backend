@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title Clube Diana - agendar sincronizacao
echo ============================================
echo   Agendar a sincronizacao automatica
echo   (a cada 15 minutos, com o Windows ligado)
echo ============================================
echo.
schtasks /create /tn "Clube Diana - Sync SIM" /sc minute /mo 15 /f /tr "\"C:\Windows\py.exe\" -3 \"%CD%\sync\clube_sync.py\" --all"
if errorlevel 1 (
  echo.
  echo NAO foi possivel agendar. Rode este arquivo como administrador
  echo ou avise o suporte.
) else (
  echo.
  echo Agendado. A loja sincroniza sozinha a cada 15 minutos.
)
echo.
schtasks /query /tn "Clube Diana - Sync SIM" /fo LIST
pause >nul
