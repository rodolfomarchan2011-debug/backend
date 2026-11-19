"""Confere se as Pessoas de teste no SIM tem qualquer referencia antes de apagar."""
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
    # quem aponta para Cliente
    cur.execute("""
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name
        JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name = 'Cliente'
    """)
    refs = cur.fetchall()
    print("tabelas que referenciam Cliente:", sorted({r["table_name"] for r in refs}))

    for doc in DOCS:
        cur.execute('SELECT "Id","Nome" FROM "Pessoa" WHERE "Documento"=%s', (doc,))
        pessoa = cur.fetchone()
        if not pessoa:
            print(f"{doc}: sem pessoa")
            continue
        cur.execute('SELECT "Id" FROM "Cliente" WHERE "PessoaId"=%s', (pessoa["Id"],))
        for c in cur.fetchall():
            total_refs = 0
            for r in refs:
                try:
                    cur.execute(f'SELECT COUNT(*) AS n FROM "{r["table_name"]}" '
                                f'WHERE "{r["column_name"]}"=%s', (c["Id"],))
                    total_refs += cur.fetchone()["n"]
                except Exception:
                    conn.rollback()
            print(f'{pessoa["Nome"]}: cliente {c["Id"][:8]} | referencias: {total_refs}')

conn.close()
