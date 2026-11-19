"""Confere se o RODOLFO entrou no SIM e se o cadastro do clube esta sincronizado."""
import io
import os

import psycopg2
import psycopg2.extras
import pymysql
import pymysql.cursors
from urllib.parse import urlparse, unquote

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOC = "418.161.998-26"


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


E = env()

# SIM
conn = psycopg2.connect(E["DIANA_SIM_VIVO_DATABASE_URL"])
conn.set_session(readonly=True, autocommit=True)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute('SELECT "Id","Nome","Documento" FROM "Pessoa" WHERE "Documento"=%s', (DOC,))
    for p in cur.fetchall():
        print("SIM Pessoa:", p["Nome"], "|", p["Id"][:8])
        cur.execute('SELECT "Id","Ativo","Eliminado" FROM "Cliente" WHERE "PessoaId"=%s', (p["Id"],))
        for c in cur.fetchall():
            print("   Cliente:", c["Id"][:8], "| ativo:", c["Ativo"], "| eliminado:", c["Eliminado"])
conn.close()

# CLUBE
p = urlparse(E["CLUBE_DATABASE_URL"])
c = pymysql.connect(host=p.hostname, port=p.port or 4000,
                    user=unquote(p.username or ""), password=unquote(p.password or ""),
                    database=(p.path or "/").lstrip("/"), charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    ssl={"ssl": {}} if "tidb" in (p.hostname or "") else None, autocommit=True)
cur = c.cursor()
cur.execute("SELECT nome, cpf, sync_status, sync_erro FROM clientes WHERE cpf=%s", ("41816199826",))
for r in cur.fetchall():
    print("CLUBE:", r["nome"], "| status:", r["sync_status"], "| erro:", r["sync_erro"])
cur.execute("SELECT COUNT(*) AS n FROM clientes WHERE sync_status <> 'sincronizado'")
print("clientes pendentes:", cur.fetchone()["n"])
c.close()
