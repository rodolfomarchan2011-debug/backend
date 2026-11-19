"""Diagnostico: o que existe hoje no clube (clientes, compras, resgates)."""
import io
import os
from urllib.parse import urlparse, unquote

import pymysql
import pymysql.cursors

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def env():
    dados = {}
    for linha in io.open(os.path.join(RAIZ, "backend", ".env"), encoding="utf-8"):
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            k, v = linha.split("=", 1)
            dados[k.strip()] = v.strip().strip('"').strip("'")
    return dados


E = env()
p = urlparse(E["CLUBE_DATABASE_URL"])
conn = pymysql.connect(host=p.hostname, port=p.port or 4000,
                       user=unquote(p.username or ""), password=unquote(p.password or ""),
                       database=(p.path or "/").lstrip("/"), charset="utf8mb4",
                       cursorclass=pymysql.cursors.DictCursor,
                       ssl={"ssl": {}} if "tidb" in (p.hostname or "") else None,
                       autocommit=True)

with conn.cursor() as cur:
    for tabela in ("clientes", "compras", "resgates", "ofertas", "newsletter"):
        try:
            cur.execute("SELECT COUNT(*) AS n FROM " + tabela)
            print(f"{tabela:12} {cur.fetchone()['n']:>4} registros")
        except Exception as exc:
            print(f"{tabela:12} erro: {type(exc).__name__}")

    print("\n--- clientes ---")
    cur.execute("SELECT id, nome, cpf, telefone, criado_em FROM clientes ORDER BY criado_em")
    for c in cur.fetchall():
        print(f"  {c['id'][:10]} | {c['nome'][:32]:32} | {c['cpf']} | {c['criado_em']}")

    print("\n--- compras ---")
    cur.execute("SELECT cliente_id, data_venda, total_liquido, cupom FROM compras ORDER BY data_venda")
    for c in cur.fetchall():
        print(f"  {c['cliente_id'][:10]} | {c['data_venda']} | R$ {float(c['total_liquido']):.2f} | cupom {c['cupom']}")

conn.close()
