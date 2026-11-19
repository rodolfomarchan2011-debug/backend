"""Procura uma conexao com usuario postgres (superusuario) nos arquivos de config."""
import io
import os
from urllib.parse import urlparse, unquote

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CANDIDATOS = [
    os.path.join(RAIZ, "backend", ".env"),
    os.path.join(RAIZ, "backend", ".env.example"),
    os.path.join(RAIZ, "..", "IA TELEGRAM", ".env"),
    os.path.join(RAIZ, "..", "..", "IA TELEGRAM", ".env"),
]

for caminho in CANDIDATOS:
    if not os.path.isfile(caminho):
        continue
    for linha in io.open(caminho, encoding="utf-8", errors="ignore"):
        linha = linha.strip()
        if "postgres" in linha.lower() and "=" in linha:
            chave, valor = linha.split("=", 1)
            chave = chave.strip()
            valor = valor.strip().strip('"').strip("'")
            if "://" in valor:
                p = urlparse(valor)
                usuario = unquote(p.username or "?")
                print(f"{os.path.basename(caminho)} | {chave} | usuario={usuario} | db={(p.path or '').lstrip('/')}")
            else:
                print(f"{os.path.basename(caminho)} | {chave} | (valor sem url)")
