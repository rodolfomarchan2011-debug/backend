"""Troca o modo de leitura da etiqueta de balanca no SIM (total <-> peso).

Faz backup da linha da Empresa antes de alterar. Uso:
    set SIM_ADMIN_SENHA=... (senha do postgres, so na sessao)
    py -3 scripts\\modo_etiqueta_balanca.py --ver
    py -3 scripts\\modo_etiqueta_balanca.py --peso
    py -3 scripts\\modo_etiqueta_balanca.py --total

A senha NUNCA fica gravada no arquivo: vem de SIM_ADMIN_URL (.env) ou da
variavel de ambiente SIM_ADMIN_SENHA.
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


def ver(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT "EtiquetaBalancaTotal", "TamanhoCodigoProdutoEtiquetaBalanca" '
                    'FROM "Empresa"')
        valor, tamanho = cur.fetchone()
        print(f"EtiquetaBalancaTotal = {valor} | TamanhoCodigoProdutoEtiquetaBalanca = {tamanho}")


def backup(conn):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT * FROM "Empresa"')
        linhas = [dict(r) for r in cur.fetchall()]
    nome = f"empresa-antes-{datetime.now():%Y%m%d-%H%M%S}.json"
    with open(os.path.join(BACKUP_DIR, nome), "w", encoding="utf-8") as fh:
        json.dump(linhas, fh, ensure_ascii=False, default=str, indent=1)
    print("backup:", nome)


def definir(conn, valor):
    with conn.cursor() as cur:
        cur.execute('UPDATE "Empresa" SET "EtiquetaBalancaTotal" = %s', (valor,))
        if cur.rowcount == 0:
            conn.rollback()
            sys.exit("Nenhuma linha da Empresa foi alterada.")
    conn.commit()
    print(f"EtiquetaBalancaTotal agora = {valor}")


def main():
    ap = argparse.ArgumentParser()
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--ver", action="store_true")
    grupo.add_argument("--peso", action="store_true")
    grupo.add_argument("--total", action="store_true")
    args = ap.parse_args()

    conn = conectar(carregar_env())
    try:
        if args.ver:
            ver(conn)
        elif args.peso:
            backup(conn)
            definir(conn, 1)
            ver(conn)
        else:
            backup(conn)
            definir(conn, 0)
            ver(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
