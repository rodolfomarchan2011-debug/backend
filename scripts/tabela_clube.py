"""Piloto da tabela de preco do clube dentro do SIM.

Cria a tabela "CLUBE TESTE" como copia da tabela normal e muda SO o preco
da PICANHA KG, para provar que a venda aplica a tabela do cliente.

Uso (na pasta backend):
    py -3 scripts\\tabela_clube.py --status
    py -3 scripts\\tabela_clube.py --criar
    py -3 scripts\\tabela_clube.py --desfazer

A conexao de escrita vem de SIM_ADMIN_URL no .env (postgres://usuario:senha@host:porta/SIM).
Sem ela, --status usa a conexao de leitura (DIANA_SIM_VIVO_DATABASE_URL).
Nunca imprime a URL nem a senha.
"""
import argparse
import os
import sys
import uuid
from datetime import datetime

import psycopg2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATOS = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "..", "IA TELEGRAM", ".env"),
    os.path.join(ROOT, "..", "..", "IA TELEGRAM", ".env"),
]

NOME_TABELA = "CLUBE TESTE"
ID_TABELA = uuid.UUID("c1b0e000-0000-4000-8000-000000000001")
PRODUTO_TESTE = "PICANHA KG"
PRECO_TESTE = 10.00
CPF_TESTE = "222.333.444-05"
NOME_TESTE = "TESTE CLUBE DIANA"


def carregar_env():
    valores = {}
    for caminho in ENV_CANDIDATOS:
        if not os.path.isfile(caminho):
            continue
        with open(caminho, encoding="utf-8", errors="ignore") as fh:
            for linha in fh:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                chave, valor = linha.split("=", 1)
                valor = valor.strip().strip('"').strip("'")
                if valor:
                    valores.setdefault(chave.strip(), valor)
    for chave, valor in os.environ.items():
        if valor:
            valores[chave] = valor
    return valores


def conectar(url, somente_leitura=False):
    conn = psycopg2.connect(url, connect_timeout=15,
                            options="-c statement_timeout=60000")
    conn.autocommit = False
    if somente_leitura:
        conn.set_session(readonly=True)
    return conn


def tabela_normal(cur):
    """A tabela de preco usada hoje pelo caixa (a que nao e a de teste)."""
    cur.execute(
        'SELECT "Id", "NomeTabela" FROM "TabelaPreco" '
        'WHERE "NomeTabela" <> %s ORDER BY "FrenteCaixa" DESC, "NomeTabela" LIMIT 1',
        (NOME_TABELA,))
    return cur.fetchone()


def status(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT "NomeTabela", count(ptp."ProdutoId") '
                    'FROM "TabelaPreco" t '
                    'LEFT JOIN "ProdutoTabelaPreco" ptp ON ptp."TabelaPrecoId" = t."Id" '
                    'GROUP BY t."NomeTabela" ORDER BY t."NomeTabela"')
        for nome, qtd in cur.fetchall():
            print(f"  tabela {nome!r}: {qtd} produtos")

        normal = tabela_normal(cur)
        if not normal:
            print("  nenhuma tabela normal encontrada")
            return
        cur.execute('SELECT ptp."PrecoVenda" FROM "ProdutoTabelaPreco" ptp '
                    'JOIN "Produto" p ON p."Id" = ptp."ProdutoId" '
                    'WHERE ptp."TabelaPrecoId" = %s AND p."NomeProduto" = %s',
                    (normal[0], PRODUTO_TESTE))
        linha = cur.fetchone()
        print(f"  {PRODUTO_TESTE} na tabela normal ({normal[1]}): "
              f"R$ {float(linha[0]):.2f}" if linha else "  produto nao encontrado")

        cur.execute('SELECT count(*) FROM "Cliente" WHERE "TabelaPrecoId" = %s',
                    (str(ID_TABELA),))
        print(f"  clientes ligados a {NOME_TABELA!r}: {cur.fetchone()[0]}")


def criar(conn):
    with conn.cursor() as cur:
        normal = tabela_normal(cur)
        if not normal:
            sys.exit("Nao achei a tabela de preco normal para copiar.")
        print(f"Origem dos precos: {normal[1]}")

        cur.execute('INSERT INTO "TabelaPreco" ("Id","NomeTabela","LetraAtalho",'
                    '"TodosProdutos","FrenteCaixa") VALUES (%s,%s,%s,true,true) '
                    'ON CONFLICT ("Id") DO NOTHING', (str(ID_TABELA), NOME_TABELA, "C"))

        cur.execute('INSERT INTO "ProdutoTabelaPreco" ("ProdutoId","TabelaPrecoId",'
                    '"Lucro","PrecoVenda") '
                    'SELECT "ProdutoId", %s, "Lucro", "PrecoVenda" '
                    'FROM "ProdutoTabelaPreco" WHERE "TabelaPrecoId" = %s '
                    'ON CONFLICT ("ProdutoId","TabelaPrecoId") DO NOTHING',
                    (str(ID_TABELA), normal[0]))

        cur.execute('UPDATE "ProdutoTabelaPreco" SET "PrecoVenda" = %s '
                    'WHERE "TabelaPrecoId" = %s AND "ProdutoId" = '
                    '(SELECT "Id" FROM "Produto" WHERE "NomeProduto" = %s)',
                    (PRECO_TESTE, str(ID_TABELA), PRODUTO_TESTE))
        if cur.rowcount == 0:
            conn.rollback()
            sys.exit(f"Nao achei o produto {PRODUTO_TESTE!r}; nada foi gravado.")

        cur.execute('SELECT count(*) FROM "ProdutoTabelaPreco" WHERE "TabelaPrecoId" = %s',
                    (str(ID_TABELA),))
        total = cur.fetchone()[0]
    conn.commit()
    print(f"OK: tabela {NOME_TABELA!r} criada com {total} produtos; "
          f"{PRODUTO_TESTE} = R$ {PRECO_TESTE:.2f}")


def desfazer(conn):
    with conn.cursor() as cur:
        cur.execute('UPDATE "Cliente" SET "TabelaPrecoId" = NULL WHERE "TabelaPrecoId" = %s',
                    (str(ID_TABELA),))
        soltos = cur.rowcount
        cur.execute('DELETE FROM "TabelaPreco" WHERE "Id" = %s', (str(ID_TABELA),))
        apagadas = cur.rowcount
    conn.commit()
    print(f"OK: {apagadas} tabela(s) apagada(s) e {soltos} cliente(s) desligado(s).")


def criar_cliente_teste(conn):
    """Cria (ou reaproveita) o cliente de teste apontando para a tabela CLUBE TESTE."""
    with conn.cursor() as cur:
        cur.execute('SELECT "Id" FROM "Pessoa" WHERE "Documento" = %s', (CPF_TESTE,))
        achado = cur.fetchone()
        if achado:
            pessoa_id = achado[0]
            cur.execute('SELECT "Id" FROM "Cliente" WHERE "PessoaId" = %s', (pessoa_id,))
            cli = cur.fetchone()
            if cli:
                cur.execute('UPDATE "Cliente" SET "TabelaPrecoId" = %s WHERE "Id" = %s',
                            (str(ID_TABELA), cli[0]))
                conn.commit()
                print(f"Cliente de teste ja existia; tabela de preco atualizada. CPF {CPF_TESTE}")
                return
            cliente_id = str(uuid.uuid4())
        else:
            pessoa_id = str(uuid.uuid4())
            cliente_id = str(uuid.uuid4())
            cur.execute('INSERT INTO "Pessoa" ("Id","Nome","Apelido","Regime",'
                        '"Documento","Telefone","DataNascimento") '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                        (pessoa_id, NOME_TESTE, "", 0, CPF_TESTE, "", None))

        cur.execute('INSERT INTO "Cliente" ("Id","PessoaId","LimiteCredito","Bloqueado",'
                    '"DataCadastro","TabelaPrecoId") VALUES (%s,%s,%s,%s,%s,%s)',
                    (cliente_id, pessoa_id, 0, False, datetime.now(), str(ID_TABELA)))
    conn.commit()
    print(f"Cliente de teste criado: {NOME_TESTE} | CPF {CPF_TESTE} | tabela {NOME_TABELA}")


def remover_cliente_teste(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT "Id" FROM "Pessoa" WHERE "Documento" = %s AND "Nome" = %s',
                    (CPF_TESTE, NOME_TESTE))
        achado = cur.fetchone()
        if not achado:
            print("Cliente de teste nao existe.")
            return
        pessoa_id = achado[0]
        cur.execute('DELETE FROM "Cliente" WHERE "PessoaId" = %s', (pessoa_id,))
        cur.execute('DELETE FROM "Pessoa" WHERE "Id" = %s', (pessoa_id,))
    conn.commit()
    print(f"Cliente de teste removido (CPF {CPF_TESTE}).")


def main():
    ap = argparse.ArgumentParser(description="Tabela de preco do clube no SIM")
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--status", action="store_true")
    grupo.add_argument("--criar", action="store_true")
    grupo.add_argument("--cliente-teste", action="store_true")
    grupo.add_argument("--desfazer", action="store_true")
    args = ap.parse_args()

    env = carregar_env()
    url = env.get("SIM_ADMIN_URL")
    if not url:
        if args.status:
            url = env.get("DIANA_SIM_VIVO_DATABASE_URL")
            if not url:
                sys.exit("Sem SIM_ADMIN_URL e sem DIANA_SIM_VIVO_DATABASE_URL no .env")
        else:
            sys.exit("Falta SIM_ADMIN_URL no backend\\.env "
                     "(postgres://usuario:senha@127.0.0.1:5432/SIM)")

    conn = conectar(url, somente_leitura=args.status)
    try:
        if args.status:
            status(conn)
        elif args.criar:
            criar(conn)
        elif args.cliente_teste:
            criar_cliente_teste(conn)
        else:
            desfazer(conn)
            remover_cliente_teste(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
