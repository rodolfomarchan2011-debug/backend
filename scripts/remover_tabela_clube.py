"""Remove a tabela de preco CLUBE DIANA do SIM (modelo antigo do clube).

Faz backup do que vai apagar antes. Uso:
    set SIM_ADMIN_SENHA=...
    py -3 scripts\\remover_tabela_clube.py --ver
    py -3 scripts\\remover_tabela_clube.py --remover
"""
import argparse
import json
import os
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(ROOT, "backups")
NOME = "CLUBE DIANA"


def carregar_env():
    valores = {}
    for caminho in (os.path.join(ROOT, ".env"),
                    os.path.join(ROOT, "..", "IA TELEGRAM", ".env")):
        if not os.path.isfile(caminho):
            continue
        with open(caminho, encoding="utf-8", errors="ignore") as fh:
            for linha in fh:
                linha = linha.strip()
                if linha and not linha.startswith("#") and "=" in linha:
                    chave, valor = linha.split("=", 1)
                    if valor.strip():
                        valores.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))
    for chave, valor in os.environ.items():
        if valor:
            valores[chave] = valor
    return valores


def conectar(env):
    url = env.get("SIM_ADMIN_URL")
    if url:
        conn = psycopg2.connect(url, connect_timeout=15)
    else:
        senha = env.get("SIM_ADMIN_SENHA")
        if not senha:
            sys.exit("Defina SIM_ADMIN_URL no .env ou SIM_ADMIN_SENHA no ambiente.")
        conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                                password=senha, dbname="SIM", connect_timeout=15)
    conn.autocommit = False
    return conn


def achar(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT * FROM "TabelaPreco" WHERE "NomeTabela" = %s', (NOME,))
        return cur.fetchall()


def resumo(conn, ids):
    with conn.cursor() as cur:
        if not ids:
            print("tabela do clube: nao existe")
            return
        cur.execute('SELECT count(*) FROM "Cliente" WHERE "TabelaPrecoId"::text = ANY(%s)', (ids,))
        cli = cur.fetchone()[0]
        cur.execute('SELECT count(*) FROM "ProdutoTabelaPreco" WHERE "TabelaPrecoId"::text = ANY(%s)', (ids,))
        pre = cur.fetchone()[0]
        print(f"tabela do clube: {len(ids)} | clientes ligados: {cli} | linhas de preco: {pre}")


def remover(conn, linhas):
    ids = [str(l["Id"]) for l in linhas]
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT "Cliente"."Id","PessoaId","TabelaPrecoId" FROM "Cliente" '
                    'WHERE "TabelaPrecoId"::text = ANY(%s)', (ids,))
        clientes = [dict(r) for r in cur.fetchall()]
    caminho = os.path.join(BACKUP_DIR, f"tabela-clube-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump({"tabelas": [dict(l) for l in linhas], "clientes": clientes},
                  fh, ensure_ascii=False, default=str, indent=1)
    print("backup:", os.path.basename(caminho))

    with conn.cursor() as cur:
        cur.execute('UPDATE "Cliente" SET "TabelaPrecoId" = NULL '
                    'WHERE "TabelaPrecoId"::text = ANY(%s)', (ids,))
        print("clientes desligados:", cur.rowcount)
        cur.execute('DELETE FROM "ProdutoTabelaPreco" WHERE "TabelaPrecoId"::text = ANY(%s)', (ids,))
        print("linhas de preco apagadas:", cur.rowcount)
        cur.execute('DELETE FROM "TabelaPreco" WHERE "Id"::text = ANY(%s)', (ids,))
        print("tabelas apagadas:", cur.rowcount)
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--ver", action="store_true")
    grupo.add_argument("--remover", action="store_true")
    args = ap.parse_args()

    conn = conectar(carregar_env())
    try:
        linhas = achar(conn)
        ids = [str(l["Id"]) for l in linhas]
        resumo(conn, ids)
        if args.remover and linhas:
            remover(conn, linhas)
            resumo(conn, [])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
