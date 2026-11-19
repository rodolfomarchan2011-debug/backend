"""Remove do SIM as 2 Pessoas/Clientes de teste (sem venda vinculada).

Faz backup das linhas em JSON antes. Usa transacao: se algo falhar, desfaz.
    py -3 backend/scripts/sim_limpar_teste.py --ver
    py -3 backend/scripts/sim_limpar_teste.py --apagar
"""
import argparse
import io
import json
import os
from datetime import datetime

import psycopg2
import psycopg2.extras

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS = ("535.415.427-80", "420.667.658-56")


def env():
    dados = {}
    for caminho in (os.path.join(RAIZ, "backend", ".env"),
                    os.path.join(RAIZ, "..", "IA TELEGRAM", ".env"),
                    os.path.join(RAIZ, "..", "..", "IA TELEGRAM", ".env")):
        if not os.path.isfile(caminho):
            continue
        for linha in io.open(caminho, encoding="utf-8", errors="ignore"):
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    dados.setdefault(k.strip(), v)
    return dados


ap = argparse.ArgumentParser()
ap.add_argument("--apagar", action="store_true")
args = ap.parse_args()

E = env()
URL_LEITURA = E["DIANA_SIM_VIVO_DATABASE_URL"]
URL_ESCRITA = E.get("DIANA_SIM_ESCRITA_URL")
conn = psycopg2.connect(URL_LEITURA)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

pessoas = []
for doc in DOCS:
    cur.execute('SELECT * FROM "Pessoa" WHERE "Documento"=%s', (doc,))
    p = cur.fetchone()
    if not p:
        print(f"{doc}: nao encontrada")
        continue
    cur.execute('SELECT * FROM "Cliente" WHERE "PessoaId"=%s', (p["Id"],))
    clientes = cur.fetchall()
    vendas = 0
    for c in clientes:
        cur.execute('SELECT COUNT(*) AS n FROM "Venda" WHERE "ClienteId"=%s', (c["Id"],))
        vendas += cur.fetchone()["n"]
    pessoas.append({"pessoa": dict(p), "clientes": [dict(c) for c in clientes], "vendas": vendas})
    print(f'{p["Nome"]} | clientes: {len(clientes)} | vendas: {vendas}')

if not args.apagar:
    print("(somente leitura — use --apagar)")
    conn.close()
    raise SystemExit(0)

if any(p["vendas"] > 0 for p in pessoas):
    print("ABORTADO: existe venda vinculada. Apague a venda no SIM antes.")
    conn.close()
    raise SystemExit(1)

pasta = os.path.join(RAIZ, "backend", "backups")
os.makedirs(pasta, exist_ok=True)
caminho = os.path.join(pasta, f"sim-teste-removido-{datetime.now():%Y%m%d-%H%M%S}.json")
io.open(caminho, "w", encoding="utf-8").write(json.dumps(pessoas, ensure_ascii=False, indent=2, default=str))
print("backup:", caminho)

try:
    connw = psycopg2.connect(URL_ESCRITA)
    curw = connw.cursor()
    for p in pessoas:
        for c in p["clientes"]:
            curw.execute('DELETE FROM "Cliente" WHERE "Id"=%s', (c["Id"],))
        curw.execute('DELETE FROM "Pessoa" WHERE "Id"=%s', (p["pessoa"]["Id"],))
    connw.commit()
    connw.close()
    print("removidos do SIM:", len(pessoas))
except Exception as exc:
    print("ERRO, nada foi apagado:", type(exc).__name__, str(exc)[:160])

conn.close()
