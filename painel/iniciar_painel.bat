@echo off
rem Sobe o painel da loja (http://127.0.0.1:4200) se ele ainda nao estiver rodando.
rem Colocado na pasta Iniciar do Windows para o caixa sempre ter o cadastro rapido.
cd /d "%~dp0.."
powershell -NoProfile -WindowStyle Hidden -Command "if (-not (Get-NetTCPConnection -LocalPort 4200 -State Listen -ErrorAction SilentlyContinue)) { Start-Process -FilePath 'pythonw' -ArgumentList '%~dp0painel.py' -WindowStyle Hidden }"
