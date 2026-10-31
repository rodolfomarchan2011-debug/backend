@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title Clube Diana - Painel da loja
echo Abrindo o painel da loja no navegador...
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:4200"
py -3 painel\painel.py
echo.
echo O painel foi fechado.
pause >nul
