"""Aplica um arquivo .sql no banco do clube (TiDB). Idempotente por natureza:
use CREATE TABLE IF NOT EXISTS / INSERT IGNORE nos arquivos.

Uso (na pasta backend):
    py -3 scripts\\aplicar_sql.py sql\\2026-10-31_ofertas.sql

Le CLUBE_DATABASE_URL do .env local e nunca imprime credenciais.
"""
import os
import sys
from urllib.parse import urlparse, unquote

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATAS = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "..", "IA TELEGRAM", ".env"),
]


def carregar_env():
    valores = {}
    for caminho in ENV_CANDIDATAS:
        if not os.path.isfile(caminho):
            continue
        with open(caminho, encoding="utf-8", errors="ignore") as fh:
            for linha in fh:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                chave, valor = linha.split("=", 1)
                valor = valor.strip().strip('"').strip("'")
                if valor:
                    valores.setdefault(chave.strip(), valor)
    for chave, valor in os.environ.items():
        if valor:
            valores[chave] = valor
    return valores


def conectar(url):
    p = urlparse(url)
    porta = p.port or 3306
    return pymysql.connect(
        host=p.hostname, port=porta,
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        database=p.path.lstrip("/") or "clube_diana",
        connect_timeout=20, autocommit=True,
        ssl={"ssl": {}} if porta == 4000 else None,
        charset="utf8mb4",
    )


def sentencas(sql):
    """Quebra o arquivo em comandos. Ignora linhas de comentario."""
    limpo = "\n".join(
        ln for ln in sql.lstrip("\ufeff").splitlines()
        if not ln.strip().startswith("--")
    )
    return [pedaco.strip() for pedaco in limpo.split(";") if pedaco.strip()]


def main():
    if len(sys.argv) < 2:
        sys.exit("Uso: py -3 scripts\\aplicar_sql.py <arquivo.sql>")
    caminho = sys.argv[1]
    if not os.path.isfile(caminho):
        sys.exit("Arquivo nao encontrado: " + caminho)

    env = carregar_env()
    url = env.get("CLUBE_DATABASE_URL")
    if not url:
        sys.exit("Defina CLUBE_DATABASE_URL no .env")

    with open(caminho, encoding="utf-8") as fh:
        comandos = sentencas(fh.read())

    p = urlparse(url)
    print("Banco alvo: %s:%s/%s | %d comandos" % (
        p.hostname, p.port or 3306, p.path.lstrip("/") or "clube_diana", len(comandos)))

    conn = conectar(url)
    try:
        with conn.cursor() as cur:
            for i, comando in enumerate(comandos, 1):
                cur.execute(comando)
                print("  [%d/%d] ok: %s" % (i, len(comandos),
                                            comando.splitlines()[0][:70]))
    finally:
        conn.close()
    print("Concluido.")


if __name__ == "__main__":
    main()
