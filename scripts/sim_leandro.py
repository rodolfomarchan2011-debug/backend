"""Confere o cliente LEANDRO no SIM (Pessoa + Cliente + vínculos)."""
import io
import os

import psycopg2
import psycopg2.extras

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


conn = psycopg2.connect(env()["DIANA_SIM_VIVO_DATABASE_URL"])
conn.set_session(readonly=True, autocommit=True)

with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute('SELECT "Id","Nome","Documento" FROM "Pessoa" WHERE "Documento"=%s', (DOC,))
    for p in cur.fetchall():
        print("Pessoa:", p["Nome"], "|", p["Id"][:8])
        cur.execute('SELECT "Id","Ativo","Eliminado" FROM "Cliente" WHERE "PessoaId"=%s', (p["Id"],))
        for c in cur.fetchall():
            print("  Cliente:", c["Id"][:8], "| ativo:", c["Ativo"], "| eliminado:", c["Eliminado"])
            cur.execute('SELECT COUNT(*) AS n FROM "Venda" WHERE "ClienteId"=%s', (c["Id"],))
            print("  vendas:", cur.fetchone()["n"])

conn.close()
