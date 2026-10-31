@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title Clube Diana - sincronizacao com o SIM
echo ============================================
echo   Sincronizacao do Clube Diana com o SIM
echo ============================================
echo.
py -3 sync\clube_sync.py --all
echo.
py -3 sync\clube_sync.py --status
echo.
echo Pode fechar esta janela.
pause >nul
