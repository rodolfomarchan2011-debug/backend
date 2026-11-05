"""Reconstroi o grupo MERCEARIA a partir dos subgrupos (nao toca em estoque/vendas).

Uso:
    set SIM_ADMIN_SENHA=...
    py -3 scripts\\arrumar_grupo_mercearia.py --ver
    py -3 scripts\\arrumar_grupo_mercearia.py --aplicar

So altera Produto.ProdutoGrupoId. Estoque, vendas, precos e notas ficam iguais.
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime

import psycopg2
import psycopg2.extras

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(ROOT, "backups")

GRUPO_UTENSILIOS = "UTENSILIOS"
GRUPO_DOCE = "MERCEARIA DOCE"
GRUPO_BEBIDAS = "BEBIDAS"
GRUPO_NOVO = "MERCEARIA"

# subgrupos (sem espacos nas pontas) que saem do UTENSILIOS
MERCEARIA_DO_UTENSILIOS = {
    "TEMPEROS", "FARINACIOS", "MOLHOS", "SALGADOS", "GERAL", "ENLATADOS",
    "CHURRASCO", "LIMPEZA GERAL", "OLEOS E AZEITES", "FRIOS", "LATICINIOS",
    "GRAOS", "MASSAS", "PAES", "EMBUTIDOS", "TEMPERADOS E GRANEL",
    "LINGUICAS", "SUINA", "DOCES", "MATINAIS",
}
BEBIDAS_DO_UTENSILIOS = {"BEBIDAS", "REFRIGERANTES", "SUCOS"}

# subgrupos que saem do MERCEARIA DOCE (nao sao doce)
MERCEARIA_DO_DOCE = {
    "LATICINIOS", "FARINACIOS", "LIMPEZA GERAL", "ENLATADOS", "TEMPEROS",
    "EMBUTIDOS", "SALGADOS", "GRAOS", "PEIXES",
}
BEBIDAS_DO_DOCE = {"SUCOS"}


def carregar_env():
    valores = {}
    for caminho in (os.path.join(ROOT, ".env"),
                    os.path.join(ROOT, "..", "IA TELEGRAM", ".env")):
        if not os.path.isfile(caminho):
            continue
        with open(caminho, encoding="utf-8", errors="ignore") as fh:
            for linha in fh:
                linha = linha.strip()
                if linha and not linha.startswith("#") and "=" in linha:
                    chave, valor = linha.split("=", 1)
                    if valor.strip():
                        valores.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))
    for chave, valor in os.environ.items():
        if valor:
            valores[chave] = valor
    return valores


def conectar(env):
    url = env.get("SIM_ADMIN_URL")
    if url:
        conn = psycopg2.connect(url, connect_timeout=15)
    else:
        senha = env.get("SIM_ADMIN_SENHA")
        if not senha:
            sys.exit("Defina SIM_ADMIN_URL no .env ou SIM_ADMIN_SENHA no ambiente.")
        conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                                password=senha, dbname="SIM", connect_timeout=15)
    conn.autocommit = False
    return conn


def grupos(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT * FROM "ProdutoGrupo"')
        return {g["NomeGrupo"]: g for g in cur.fetchall()}


def subgrupos(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT "Id","NomeSubgrupo" FROM "ProdutoSubgrupo"')
        mapa = {}
        for s in cur.fetchall():
            chave = (s["NomeSubgrupo"] or "").strip()
            if chave in mapa:
                sys.exit(f"Subgrupo duplicado depois de tirar espacos: {chave!r}")
            mapa[chave] = str(s["Id"])
        return mapa


def contar(conn, grupo_id, subs):
    with conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM "Produto" WHERE "ProdutoGrupoId" = %s '
                    'AND "ProdutoSubgrupoId"::text = ANY(%s) AND "Eliminado" = false',
                    (grupo_id, list(subs)))
        return cur.fetchone()[0]


def plano(conn):
    g = grupos(conn)
    s = subgrupos(conn)
    faltando = [n for n in (GRUPO_UTENSILIOS, GRUPO_DOCE, GRUPO_BEBIDAS) if n not in g]
    if faltando:
        sys.exit(f"Grupos nao encontrados: {faltando}")

    def ids(nomes):
        ids_ = []
        for n in sorted(nomes):
            if n not in s:
                sys.exit(f"Subgrupo nao encontrado: {n!r}")
            ids_.append(s[n])
        return ids_

    return {
        "utensilios": str(g[GRUPO_UTENSILIOS]["Id"]),
        "doce": str(g[GRUPO_DOCE]["Id"]),
        "bebidas": str(g[GRUPO_BEBIDAS]["Id"]),
        "novo_existe": GRUPO_NOVO in g,
        "novo_id": str(g[GRUPO_NOVO]["Id"]) if GRUPO_NOVO in g else None,
        "modelo": g[GRUPO_UTENSILIOS],
        "subs_mercearia_ut": ids(MERCEARIA_DO_UTENSILIOS),
        "subs_bebidas_ut": ids(BEBIDAS_DO_UTENSILIOS),
        "subs_mercearia_dc": ids(MERCEARIA_DO_DOCE),
        "subs_bebidas_dc": ids(BEBIDAS_DO_DOCE),
    }


def mostrar(conn, p):
    print("MERCEARIA (novo):", "ja existe" if p["novo_existe"] else "vai ser criado")
    print("  saem do UTENSILIOS:", contar(conn, p["utensilios"], p["subs_mercearia_ut"]))
    print("  saem do MERCEARIA DOCE:", contar(conn, p["doce"], p["subs_mercearia_dc"]))
    print("BEBIDAS (grupo existente):")
    print("  saem do UTENSILIOS:", contar(conn, p["utensilios"], p["subs_bebidas_ut"]))
    print("  saem do MERCEARIA DOCE:", contar(conn, p["doce"], p["subs_bebidas_dc"]))


def aplicar(conn, p):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ids_tocados = [p["utensilios"], p["doce"]]
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT "Id","NomeProduto","ProdutoGrupoId","ProdutoSubgrupoId" '
                    'FROM "Produto" WHERE "ProdutoGrupoId"::text = ANY(%s)', (ids_tocados,))
        antes = [dict(r) for r in cur.fetchall()]
    caminho = os.path.join(BACKUP_DIR, f"grupos-antes-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump(antes, fh, ensure_ascii=False, default=str, indent=1)
    print("backup:", os.path.basename(caminho), f"({len(antes)} produtos)")

    novo_id = p["novo_id"]
    if not novo_id:
        novo_id = str(uuid.uuid4())
        modelo = p["modelo"]
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO "ProdutoGrupo" ("Id","NomeGrupo","PermiteLancarTotal",'
                '"EditaPreco","Favorito","ReceitaOptica","Eliminado",'
                '"PorcentagemDescontoMaximo","PorcentagemComissao","ObservacaoItem") '
                'VALUES (%s,%s,%s,%s,%s,%s,false,%s,%s,%s)',
                (novo_id, GRUPO_NOVO, modelo["PermiteLancarTotal"], modelo["EditaPreco"],
                 modelo["Favorito"], modelo["ReceitaOptica"],
                 modelo["PorcentagemDescontoMaximo"], modelo["PorcentagemComissao"],
                 modelo["ObservacaoItem"]))
        print("grupo criado:", GRUPO_NOVO, novo_id)

    movimentos = [
        (novo_id, p["utensilios"], p["subs_mercearia_ut"], "UTENSILIOS -> MERCEARIA"),
        (p["bebidas"], p["utensilios"], p["subs_bebidas_ut"], "UTENSILIOS -> BEBIDAS"),
        (novo_id, p["doce"], p["subs_mercearia_dc"], "MERCEARIA DOCE -> MERCEARIA"),
        (p["bebidas"], p["doce"], p["subs_bebidas_dc"], "MERCEARIA DOCE -> BEBIDAS"),
    ]
    with conn.cursor() as cur:
        for destino, origem, subs, rotulo in movimentos:
            cur.execute('UPDATE "Produto" SET "ProdutoGrupoId" = %s '
                        'WHERE "ProdutoGrupoId" = %s AND "ProdutoSubgrupoId"::text = ANY(%s)',
                        (destino, origem, subs))
            print(f"  {rotulo}: {cur.rowcount}")
    conn.commit()


def resumo_final(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT g."NomeGrupo", count(p."Id")
                       FROM "ProdutoGrupo" g
                       LEFT JOIN "Produto" p ON p."ProdutoGrupoId" = g."Id" AND p."Eliminado" = false
                       GROUP BY g."Id", g."NomeGrupo" ORDER BY g."NomeGrupo" """)
        print("\ndepois:")
        for nome, qtd in cur.fetchall():
            print(f"   {nome!r:22} {qtd}")


def main():
    ap = argparse.ArgumentParser()
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--ver", action="store_true")
    grupo.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    conn = conectar(carregar_env())
    try:
        p = plano(conn)
        mostrar(conn, p)
        if args.aplicar:
            aplicar(conn, p)
            resumo_final(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
