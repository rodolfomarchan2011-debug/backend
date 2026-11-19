"""Verifica permissoes do usuario de escrita nas tabelas Pessoa/Cliente (nomes com aspas)."""
import io
import os

import psycopg2

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
conn = psycopg2.connect(E["DIANA_SIM_ESCRITA_URL"])
conn.set_session(autocommit=True)
cur = conn.cursor()

for tabela in ('"Pessoa"', '"Cliente"'):
    for acao in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        cur.execute("SELECT has_table_privilege(%s, %s)", (tabela, acao))
        print(f"{tabela:10} {acao:7} {cur.fetchone()[0]}")

cur.execute('SELECT column_name FROM information_schema.columns '
            'WHERE table_name=%s ORDER BY ordinal_position', ("Cliente",))
print("\nCliente colunas:", [r[0] for r in cur.fetchall()])

cur.execute('SELECT "Id","Nome","Documento" FROM "Pessoa" WHERE "Documento"=%s',
            ("418.161.998-26",))
for r in cur.fetchall():
    print("Pessoa:", r)

conn.close()
