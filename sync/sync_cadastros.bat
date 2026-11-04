@echo off
rem Envia os cadastros novos do clube para o SIM.
rem Roda pelo Agendador de Tarefas (Clube Diana - Cadastros), de minuto em minuto,
rem para o cliente que acabou de se cadastrar no site ja aparecer no caixa.
cd /d "%~dp0.."
"C:\Windows\py.exe" -3 "%~dp0clube_sync.py" --push-cadastros
