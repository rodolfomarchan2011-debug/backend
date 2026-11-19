"""Busca no SIM por nome (ELPIDIO / SILVANA) e a venda de teste."""
import io
import os

import psycopg2
import psycopg2.extras

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


url = env().get("DIANA_SIM_VIVO_DATABASE_URL")
conn = psycopg2.connect(url)
conn.set_session(readonly=True, autocommit=True)

with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    for termo in ("ELPIDIO", "SILVANA"):
        cur.execute('SELECT "Id","Nome","Documento","Celular" FROM "Pessoa" '
                    'WHERE upper("Nome") LIKE %s', ("%" + termo + "%",))
        linhas = cur.fetchall()
        print(f"{termo}: {len(linhas)} pessoa(s)")
        for p in linhas:
            print("   ", p)

    print("\n--- ultimas 5 vendas ---")
    cur.execute('SELECT "Id","ClienteId","TotalLiquido","Cupom","DataVenda" FROM "Venda" '
                'ORDER BY "DataVenda" DESC LIMIT 5')
    for v in cur.fetchall():
        print("   ", v)

conn.close()
