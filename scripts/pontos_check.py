"""Valida as consultas de pontos direto no banco (sem criar conta)."""
import os
import sys
from urllib.parse import urlparse, unquote

import pymysql
import pymysql.cursors

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
env = {}
caminho = os.path.join(RAIZ, "backend", ".env")
for linha in open(caminho, encoding="utf-8"):
    linha = linha.strip()
    if linha and not linha.startswith("#") and "=" in linha:
        k, v = linha.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

p = urlparse(env["CLUBE_DATABASE_URL"])
conn = pymysql.connect(host=p.hostname, port=p.port or 4000,
                       user=unquote(p.username or ""), password=unquote(p.password or ""),
                       database=(p.path or "/").lstrip("/"), charset="utf8mb4",
                       cursorclass=pymysql.cursors.DictCursor,
                       ssl={"ssl": {}} if "tidb" in (p.hostname or "") else None,
                       autocommit=True)

with conn.cursor() as cur:
    # pega um cliente real que tenha compras
    cur.execute("SELECT cliente_id, COUNT(*) n, SUM(total_liquido) total FROM compras "
                "GROUP BY cliente_id ORDER BY n DESC LIMIT 3")
    clientes = cur.fetchall()
    print("clientes com compras:", [(c["cliente_id"][:8], c["n"]) for c in clientes])

    for c in clientes:
        cid = c["cliente_id"]
        cur.execute(
            "SELECT COALESCE(SUM(FLOOR(total_liquido * %s)),0) AS p FROM compras "
            "WHERE cliente_id=%s AND data_venda >= DATE_SUB(NOW(), INTERVAL %s MONTH)",
            (0.5, cid, 12))
        ganhos = int(cur.fetchone()["p"] or 0)
        cur.execute("SELECT COALESCE(SUM(pontos),0) AS p FROM resgates "
                    "WHERE cliente_id=%s AND status <> 'cancelado'", (cid,))
        usados = int(cur.fetchone()["p"] or 0)
        print(f"  {cid[:8]}: gasto R$ {float(c['total']):.2f} -> ganhos {ganhos} pts | usados {usados} | saldo {max(0, ganhos - usados)}")

    cur.execute("SELECT id, nome, pontos FROM brindes WHERE ativo=1 ORDER BY ordem")
    print("catalogo:", [(b["nome"][:22], b["pontos"]) for b in cur.fetchall()])

conn.close()
print("consultas ok")
