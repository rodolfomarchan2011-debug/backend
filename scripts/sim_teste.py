"""Mostra no SIM as Pessoas/Clientes de teste e se tem venda vinculada."""
import io
import os

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


conn = psycopg2.connect(env()["DIANA_SIM_VIVO_DATABASE_URL"])
conn.set_session(readonly=True, autocommit=True)

with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    for doc in DOCS:
        cur.execute('SELECT "Id","Nome","Documento" FROM "Pessoa" WHERE "Documento"=%s', (doc,))
        pessoa = cur.fetchone()
        if not pessoa:
            print(f"{doc}: pessoa nao encontrada")
            continue

        cur.execute('SELECT "Id" FROM "Cliente" WHERE "PessoaId"=%s', (pessoa["Id"],))
        clientes = [r["Id"] for r in cur.fetchall()]

        vendas = 0
        if clientes:
            marcas = ",".join(["%s"] * len(clientes))
            cur.execute(f'SELECT COUNT(*) AS n FROM "Venda" WHERE "ClienteId" IN ({marcas})',
                        clientes)
            vendas = cur.fetchone()["n"]

        print(f'{pessoa["Nome"]} | doc {pessoa["Documento"]} | clientes SIM: {len(clientes)} | vendas: {vendas}')

conn.close()
