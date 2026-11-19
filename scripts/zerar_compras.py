"""Zera as compras do clube (com backup) e ajusta o watermark para hoje.

Só mexe no banco do CLUBE (TiDB) — nada no SIM.
    py -3 backend/scripts/zerar_compras.py --ver
    py -3 backend/scripts/zerar_compras.py --zerar
"""
import argparse
import io
import json
import os
from datetime import datetime
from urllib.parse import urlparse, unquote

import pymysql
import pymysql.cursors

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOJE = "2026-11-18 00:00:00"

ap = argparse.ArgumentParser()
ap.add_argument("--zerar", action="store_true")
args = ap.parse_args()


def env():
    dados = {}
    for linha in io.open(os.path.join(RAIZ, "backend", ".env"), encoding="utf-8"):
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            k, v = linha.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                dados.setdefault(k.strip(), v)
    return dados


p = urlparse(env()["CLUBE_DATABASE_URL"])
conn = pymysql.connect(host=p.hostname, port=p.port or 4000,
                       user=unquote(p.username or ""), password=unquote(p.password or ""),
                       database=(p.path or "/").lstrip("/"), charset="utf8mb4",
                       cursorclass=pymysql.cursors.DictCursor,
                       ssl={"ssl": {}} if "tidb" in (p.hostname or "") else None,
                       autocommit=True)

with conn.cursor() as cur:
    cur.execute("SELECT c.data_venda, c.total_liquido, cl.nome "
                "FROM compras c LEFT JOIN clientes cl ON cl.id=c.cliente_id "
                "ORDER BY c.data_venda")
    compras = cur.fetchall()
    print(f"compras hoje: {len(compras)}")
    for c in compras:
        print(f"  {c['data_venda']} | R$ {float(c['total_liquido']):.2f} | {c['nome']}")

    cur.execute("SELECT valor FROM sync_control WHERE chave='compras_watermark'")
    wm = cur.fetchone()
    print("watermark atual:", wm["valor"] if wm else "(nenhum)")

    if not args.zerar:
        print("(somente leitura — use --zerar)")
        conn.close()
        raise SystemExit(0)

    pasta = os.path.join(RAIZ, "backend", "backups")
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, f"compras-zeradas-{datetime.now():%Y%m%d-%H%M%S}.json")
    io.open(caminho, "w", encoding="utf-8").write(
        json.dumps(compras, ensure_ascii=False, indent=2, default=str))
    print("backup:", caminho)

    cur.execute("DELETE FROM compras")
    print("compras apagadas:", cur.rowcount)

    cur.execute("INSERT INTO sync_control (chave, valor) VALUES ('compras_watermark', %s) "
                "ON DUPLICATE KEY UPDATE valor=VALUES(valor)", (HOJE,))
    print("watermark ajustado para", HOJE)

    cur.execute("SELECT COUNT(*) AS n FROM compras")
    print("compras restantes:", cur.fetchone()["n"])

conn.close()
