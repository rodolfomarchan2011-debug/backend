@echo off
rem Envia os codigos de redefinicao de senha pelo WhatsApp da loja.
rem Roda pelo Agendador de Tarefas (Clube Diana - Codigos), de minuto em minuto.
cd /d "%~dp0.."
"C:\Windows\py.exe" -3 "%~dp0envia_codigos.py"
