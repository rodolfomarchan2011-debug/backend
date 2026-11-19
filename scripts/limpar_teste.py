"""Apaga os 2 cadastros de teste (Silvana e Elpidio) e a venda do Elpidio.

Faz backup em JSON antes de apagar. Nao mexe no SIM.
    py -3 backend/scripts/limpar_teste.py --ver      (so mostra)
    py -3 backend/scripts/limpar_teste.py --apagar   (apaga de verdade)
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
CPFS = ("53541542780", "42066765856")  # Silvana e Elpidio


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

ap = argparse.ArgumentParser()
ap.add_argument("--apagar", action="store_true")
ap.add_argument("--ver", action="store_true")
args = ap.parse_args()

with conn.cursor() as cur:
    cur.execute("SELECT id, nome, cpf, criado_em FROM clientes WHERE cpf IN (%s,%s)", CPFS)
    clientes = cur.fetchall()
    print(f"clientes de teste encontrados: {len(clientes)}")
    for c in clientes:
        print(f"  {c['id'][:10]} | {c['nome']} | {c['cpf']} | {c['criado_em']}")

    ids = [c["id"] for c in clientes]
    compras = []
    if ids:
        marcadores = ",".join(["%s"] * len(ids))
        cur.execute(f"SELECT * FROM compras WHERE cliente_id IN ({marcadores})", ids)
        compras = cur.fetchall()
    print(f"compras desses clientes: {len(compras)}")
    for c in compras:
        print(f"  {c['cliente_id'][:10]} | {c['data_venda']} | R$ {float(c['total_liquido']):.2f}")

    if not args.apagar:
        print("\n(somente leitura — use --apagar para remover)")
        conn.close()
        raise SystemExit(0)

    # backup
    pasta = os.path.join(RAIZ, "backend", "backups")
    os.makedirs(pasta, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    caminho = os.path.join(pasta, f"teste-removido-{carimbo}.json")
    with io.open(caminho, "w", encoding="utf-8") as fh:
        json.dump({"clientes": clientes, "compras": compras}, fh,
                  ensure_ascii=False, indent=2, default=str)
    print("backup:", caminho)

    if ids:
        marcadores = ",".join(["%s"] * len(ids))
        cur.execute(f"DELETE FROM compras WHERE cliente_id IN ({marcadores})", ids)
        print("compras apagadas:", cur.rowcount)
        cur.execute(f"DELETE FROM resgates WHERE cliente_id IN ({marcadores})", ids)
        print("resgates apagados:", cur.rowcount)
        cur.execute(f"DELETE FROM clientes WHERE id IN ({marcadores})", ids)
        print("clientes apagados:", cur.rowcount)

    cur.execute("SELECT COUNT(*) AS n FROM clientes")
    print("clientes restantes:", cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) AS n FROM compras")
    print("compras restantes:", cur.fetchone()["n"])

conn.close()
