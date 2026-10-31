@echo off
rem Executado pelo Agendador de Tarefas do Windows (Clube Diana - Sync SIM)
cd /d "%~dp0.."
"C:\Windows\py.exe" -3 "%~dp0clube_sync.py" --all
