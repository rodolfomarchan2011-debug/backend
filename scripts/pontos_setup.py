"""Cria as tabelas de pontos/brindes e cadastra o catalogo inicial.

Uso:
    py -3 backend/scripts/pontos_setup.py            (cria e atualiza)
    py -3 backend/scripts/pontos_setup.py --ver      (so mostra o que existe)
"""
import argparse
import os
import sys
from urllib.parse import urlparse, unquote

import pymysql
import pymysql.cursors

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ler_env():
    caminho = os.path.join(RAIZ, "backend", ".env")
    dados = {}
    if os.path.exists(caminho):
        for linha in open(caminho, encoding="utf-8"):
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            dados[k.strip()] = v.strip().strip('"').strip("'")
    return dados


def conexao():
    env = ler_env()
    url = env.get("CLUBE_DATABASE_URL") or env.get("DIANA_SIM_ESCRITA_URL")
    if not url:
        sys.exit("CLUBE_DATABASE_URL nao encontrada no backend/.env")
    p = urlparse(url)
    return pymysql.connect(
        host=p.hostname,
        port=p.port or 4000,
        user=unquote(p.username or ""),
        password=unquote(p.password or ""),
        database=(p.path or "/").lstrip("/") or None,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        ssl={"ssl": {}} if "tidb" in (p.hostname or "") else None,
        autocommit=True,
    )


TABELAS = [
    """CREATE TABLE IF NOT EXISTS brindes (
        id VARCHAR(40) NOT NULL PRIMARY KEY,
        nome VARCHAR(120) NOT NULL,
        valor DECIMAL(10,2) NOT NULL DEFAULT 0,
        pontos INT NOT NULL,
        ativo TINYINT NOT NULL DEFAULT 1,
        ordem INT NOT NULL DEFAULT 0,
        criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS resgates (
        id VARCHAR(40) NOT NULL PRIMARY KEY,
        cliente_id VARCHAR(40) NOT NULL,
        brinde_id VARCHAR(40) NOT NULL,
        brinde_nome VARCHAR(120) NOT NULL,
        pontos INT NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'pendente',
        codigo VARCHAR(12) NOT NULL,
        criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        entregue_em DATETIME NULL,
        KEY idx_resgates_cliente (cliente_id),
        KEY idx_resgates_status (status)
    )""",
]

BRINDES = [
    ("farofa", "Farofa Nobre Sabor", 9.99, 500, 1),
    ("pao-alho", "Pão de alho Max", 13.90, 700, 2),
    ("molho", "Molho para churrasco Sol Nascente", 13.99, 700, 3),
    ("coca", "Coca-Cola 2 litros", 13.99, 700, 4),
    ("sal", "Sal de Parrilla", 16.99, 850, 5),
    ("brinquedo", "Brinquedo (preço único)", 19.99, 1000, 6),
    ("carvao", "Carvão Palmeira", 21.90, 1100, 7),
    ("espeto", "Espeto para churrasco (alumínio galvanizado)", 34.90, 1750, 8),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", action="store_true")
    args = ap.parse_args()

    conn = conexao()
    try:
        with conn.cursor() as cur:
            if args.ver:
                for t in ("brindes", "resgates"):
                    try:
                        cur.execute("SELECT COUNT(*) AS n FROM " + t)
                        print(f"{t}: {cur.fetchone()['n']} registros")
                    except Exception as exc:
                        print(f"{t}: nao existe ({type(exc).__name__})")
                try:
                    cur.execute("SELECT nome, valor, pontos FROM brindes ORDER BY ordem")
                    for b in cur.fetchall():
                        print(f"  {b['pontos']:>5} pts | R$ {b['valor']:>6} | {b['nome']}")
                except Exception:
                    pass
                return

            for sql in TABELAS:
                cur.execute(sql)
                print("tabela ok")

            for bid, nome, valor, pontos, ordem in BRINDES:
                cur.execute(
                    "INSERT INTO brindes (id, nome, valor, pontos, ativo, ordem) "
                    "VALUES (%s,%s,%s,%s,1,%s) "
                    "ON DUPLICATE KEY UPDATE nome=VALUES(nome), valor=VALUES(valor), "
                    "pontos=VALUES(pontos), ordem=VALUES(ordem), ativo=1",
                    (bid, nome, valor, pontos, ordem))
            print(f"{len(BRINDES)} brindes cadastrados")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
