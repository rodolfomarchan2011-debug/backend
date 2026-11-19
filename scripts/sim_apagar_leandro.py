"""Apaga APENAS a Pessoa/Cliente LEANDRO (CPF 418.161.998-26) do SIM.

Autorizado pelo dono no chat. Nao toca em mais nada.
    py -3 backend/scripts/sim_apagar_leandro.py            (so mostra)
    py -3 backend/scripts/sim_apagar_leandro.py --apagar   (apaga)
"""
import argparse
import io
import json
import os
from datetime import datetime

import psycopg2
import psycopg2.extras

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOC = "418.161.998-26"
NOME_ESPERADO = "LEANDRO"
SENHAS = ("dianad", "DianaD", "dianaD")

ap = argparse.ArgumentParser()
ap.add_argument("--apagar", action="store_true")
args = ap.parse_args()


def conectar():
    for senha in SENHAS:
        try:
            conn = psycopg2.connect(host="127.0.0.1", port=5432, dbname="SIM",
                                    user="postgres", password=senha,
                                    connect_timeout=8)
            print("conectado como postgres")
            return conn
        except Exception:
            continue
    raise SystemExit("nao consegui conectar como postgres com as senhas informadas")


conn = conectar()
conn.autocommit = False
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute('SELECT "Id","Nome","Documento" FROM "Pessoa" WHERE "Documento"=%s', (DOC,))
pessoas = cur.fetchall()

if len(pessoas) != 1:
    print(f"ABORTADO: encontrei {len(pessoas)} pessoa(s) com esse CPF (esperava 1).")
    conn.close()
    raise SystemExit(1)

pessoa = pessoas[0]
print("Pessoa:", pessoa["Nome"], "|", pessoa["Id"])

if NOME_ESPERADO not in (pessoa["Nome"] or "").upper():
    print("ABORTADO: o nome nao contem", NOME_ESPERADO)
    conn.close()
    raise SystemExit(1)

cur.execute('SELECT * FROM "Cliente" WHERE "PessoaId"=%s', (pessoa["Id"],))
clientes = cur.fetchall()
total_vendas = 0
for c in clientes:
    cur.execute('SELECT COUNT(*) AS n FROM "Venda" WHERE "ClienteId"=%s', (c["Id"],))
    total_vendas += cur.fetchone()["n"]
print("clientes:", len(clientes), "| vendas vinculadas:", total_vendas)

if total_vendas > 0:
    print("ABORTADO: existe venda vinculada. Nao vou apagar.")
    conn.close()
    raise SystemExit(1)

if not args.apagar:
    print("(somente leitura — use --apagar para remover)")
    conn.close()
    raise SystemExit(0)

pasta = os.path.join(RAIZ, "backend", "backups")
os.makedirs(pasta, exist_ok=True)
caminho = os.path.join(pasta, f"sim-leandro-removido-{datetime.now():%Y%m%d-%H%M%S}.json")
io.open(caminho, "w", encoding="utf-8").write(
    json.dumps({"pessoa": dict(pessoa), "clientes": [dict(c) for c in clientes]},
               ensure_ascii=False, indent=2, default=str))
print("backup:", caminho)

try:
    for c in clientes:
        cur.execute('DELETE FROM "Cliente" WHERE "Id"=%s', (c["Id"],))
        print("cliente apagado:", c["Id"][:8])
    cur.execute('DELETE FROM "Pessoa" WHERE "Id"=%s', (pessoa["Id"],))
    print("pessoa apagada:", pessoa["Id"][:8])
    conn.commit()
    print("OK — commit feito")
except Exception as exc:
    conn.rollback()
    print("ERRO, nada foi apagado:", type(exc).__name__, str(exc)[:200])

conn.close()
