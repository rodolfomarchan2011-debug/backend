"""Lista os resgates pendentes e marca como entregue (uso no balcao).

    py -3 backend/scripts/pontos_pendentes.py
    py -3 backend/scripts/pontos_pendentes.py --entregar DIA-1A2B
"""
import argparse
import os
from urllib.parse import urlparse, unquote

import pymysql
import pymysql.cursors

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def conexao():
    env = {}
    caminho = os.path.join(RAIZ, "backend", ".env")
    for linha in open(caminho, encoding="utf-8"):
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            k, v = linha.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    p = urlparse(env["CLUBE_DATABASE_URL"])
    return pymysql.connect(host=p.hostname, port=p.port or 4000,
                           user=unquote(p.username or ""), password=unquote(p.password or ""),
                           database=(p.path or "/").lstrip("/"), charset="utf8mb4",
                           cursorclass=pymysql.cursors.DictCursor,
                           ssl={"ssl": {}} if "tidb" in (p.hostname or "") else None,
                           autocommit=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entregar", help="codigo do resgate, ex.: DIA-1A2B")
    args = ap.parse_args()

    conn = conexao()
    try:
        with conn.cursor() as cur:
            if args.entregar:
                cur.execute(
                    "UPDATE resgates SET status='entregue', entregue_em=NOW() "
                    "WHERE codigo=%s AND status='pendente'", (args.entregar.strip().upper(),))
                if cur.rowcount:
                    print(f"Resgate {args.entregar} marcado como ENTREGUE.")
                else:
                    print(f"Nenhum resgate pendente com o codigo {args.entregar}.")
                return

            cur.execute(
                "SELECT r.codigo, r.brinde_nome, r.pontos, r.criado_em, c.nome, c.cpf "
                "FROM resgates r LEFT JOIN clientes c ON c.id = r.cliente_id "
                "WHERE r.status='pendente' ORDER BY r.criado_em")
            linhas = cur.fetchall()

            if not linhas:
                print("Nenhum resgate pendente.")
                return

            print(f"{len(linhas)} resgate(s) pendente(s):\n")
            for r in linhas:
                cpf = r["cpf"] or ""
                if len(cpf) == 11:
                    cpf = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
                print(f"  {r['codigo']}  {r['criado_em']:%d/%m %H:%M}  "
                      f"{r['pontos']:>5} pts  {r['brinde_nome']}")
                print(f"          cliente: {r['nome'] or '—'}  CPF {cpf}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
