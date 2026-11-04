"""Mantem a tabela de preco CLUBE DIANA no SIM alinhada com as ofertas do site.

Como funciona o desconto no caixa (confirmado em 04/11/2026):
  o SIM aplica a tabela de preco do cliente assim que o cliente e escolhido
  na venda. Entao: cliente do clube -> tabela "CLUBE DIANA"; nessa tabela
  so os itens com oferta ativa tem preco diferente. O resto e copia fiel da
  tabela normal.

O que este script faz a cada rodada:
  1. garante a tabela "CLUBE DIANA" (copia completa da tabela normal);
  2. copia para ela os produtos que ainda nao existem la;
  3. grava o preco de membro nos itens com oferta ativa;
  4. devolve o preco normal nos itens que sairam da oferta;
  5. liga os clientes do clube a essa tabela (Cliente.TabelaPrecoId);
  6. desliga quem saiu do clube.

Uso:
    py -3 sync\\precos_clube.py            (aplica)
    py -3 sync\\precos_clube.py --dry-run  (so mostra o que mudaria)

Conexoes: SIM_PRECO_URL (usuario clube_preco, so preco) e CLUBE_DATABASE_URL.
"""
import os
import sys
from urllib.parse import urlparse, unquote

import pymysql
import psycopg2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATOS = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "..", "IA TELEGRAM", ".env"),
    os.path.join(ROOT, "..", "..", "IA TELEGRAM", ".env"),
]
LOG_PATH = os.path.join(ROOT, "logs", "precos.log")
NOME_TABELA = "CLUBE DIANA"
LETRA = "C"


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


def log(mensagem):
    from datetime import datetime
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {mensagem}"
    print(linha)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(linha + "\n")
    except OSError:
        pass


def conectar_clube(env):
    p = urlparse(env["CLUBE_DATABASE_URL"])
    return pymysql.connect(
        host=p.hostname, port=p.port or 3306,
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        database=p.path.lstrip("/") or "clube_diana",
        connect_timeout=20, autocommit=False,
        ssl={"ssl": {}} if (p.port or 3306) == 4000 else None,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)


def conectar_sim(env):
    url = env.get("SIM_PRECO_URL")
    if not url:
        raise RuntimeError("Falta SIM_PRECO_URL no .env "
                           "(rode scripts\\criar_usuario_preco.py uma vez)")
    return psycopg2.connect(url, connect_timeout=20)


def fmt_cpf(digitos):
    d = "".join(c for c in (digitos or "") if c.isdigit())
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else (digitos or "")


def tabela_normal(cur):
    cur.execute('SELECT "Id", "NomeTabela" FROM "TabelaPreco" '
                'WHERE "NomeTabela" <> %s ORDER BY "FrenteCaixa" DESC, "NomeTabela" LIMIT 1',
                (NOME_TABELA,))
    return cur.fetchone()


def tabela_clube(cur, criar):
    cur.execute('SELECT "Id" FROM "TabelaPreco" WHERE "NomeTabela" = %s', (NOME_TABELA,))
    achada = cur.fetchone()
    if achada:
        return achada[0]
    if not criar:
        return None
    novo = "c1b0e000-0000-4000-8000-000000000002"
    cur.execute('INSERT INTO "TabelaPreco" ("Id","NomeTabela","LetraAtalho",'
                '"TodosProdutos","FrenteCaixa") VALUES (%s,%s,%s,true,true)',
                (novo, NOME_TABELA, LETRA))
    return novo


def sincronizar(dry_run=False):
    env = carregar_env()
    sim = conectar_sim(env)
    clube = conectar_clube(env)
    try:
        with sim.cursor() as cur:
            normal = tabela_normal(cur)
            if not normal:
                raise RuntimeError("Nao achei a tabela de preco normal no SIM")
            id_normal = normal[0]
            id_clube = tabela_clube(cur, criar=not dry_run)
            if not id_clube:
                log("[precos] tabela CLUBE DIANA ainda nao existe (dry-run).")
                return 0

            # 1) copia produtos que faltam na tabela do clube
            if not dry_run:
                cur.execute('INSERT INTO "ProdutoTabelaPreco" ("ProdutoId","TabelaPrecoId",'
                            '"Lucro","PrecoVenda") '
                            'SELECT "ProdutoId", %s, "Lucro", "PrecoVenda" '
                            'FROM "ProdutoTabelaPreco" WHERE "TabelaPrecoId" = %s '
                            'ON CONFLICT ("ProdutoId","TabelaPrecoId") DO NOTHING',
                            (id_clube, id_normal))
                copiados = cur.rowcount

            # 2) precos normais e precos atuais do clube
            cur.execute('SELECT "ProdutoId"::text, "PrecoVenda" FROM "ProdutoTabelaPreco" '
                        'WHERE "TabelaPrecoId" = %s', (id_normal,))
            preco_normal = {pid: float(preco or 0) for pid, preco in cur.fetchall()}
            cur.execute('SELECT "ProdutoId"::text, "PrecoVenda" FROM "ProdutoTabelaPreco" '
                        'WHERE "TabelaPrecoId" = %s', (id_clube,))
            preco_clube = {pid: float(preco or 0) for pid, preco in cur.fetchall()}

        # 3) ofertas ativas do clube (banco do clube)
        with clube.cursor() as cur:
            cur.execute("SELECT sim_produto_id, produto, preco_por FROM ofertas "
                        "WHERE ativo=1 AND sim_produto_id IS NOT NULL "
                        "AND sim_produto_id <> ''")
            ofertas = {str(r["sim_produto_id"]): (r["produto"], float(r["preco_por"]))
                       for r in cur.fetchall()}
            cur.execute("SELECT cpf FROM clientes WHERE excluido_em IS NULL "
                        "AND sim_cliente_id IS NOT NULL")
            cpfs = [fmt_cpf(r["cpf"]) for r in cur.fetchall()]

        desejado = {}
        for pid, preco in preco_normal.items():
            desejado[pid] = ofertas.get(pid, (None, preco))[1]

        mudancas = 0
        with sim.cursor() as cur:
            for pid, preco in desejado.items():
                atual = preco_clube.get(pid)
                if atual is not None and abs(atual - preco) < 0.005:
                    continue
                mudancas += 1
                if dry_run:
                    continue
                cur.execute('UPDATE "ProdutoTabelaPreco" SET "PrecoVenda" = %s '
                            'WHERE "TabelaPrecoId" = %s AND "ProdutoId" = %s',
                            (preco, id_clube, pid))

            # 4) produto que sumiu da tabela normal sai da do clube
            sobrando = [pid for pid in preco_clube if pid not in preco_normal]
            if sobrando and not dry_run:
                cur.execute('DELETE FROM "ProdutoTabelaPreco" WHERE "TabelaPrecoId" = %s '
                            'AND "ProdutoId"::text = ANY(%s)', (id_clube, sobrando))

            # 5) liga os clientes do clube e desliga quem saiu
            if cpfs and not dry_run:
                cur.execute('UPDATE "Cliente" SET "TabelaPrecoId" = %s '
                            'WHERE "PessoaId" IN (SELECT "Id" FROM "Pessoa" '
                            'WHERE "Documento" = ANY(%s)) '
                            'AND ("TabelaPrecoId" IS NULL OR "TabelaPrecoId" <> %s)',
                            (id_clube, cpfs, id_clube))
                ligados = cur.rowcount
                cur.execute('UPDATE "Cliente" SET "TabelaPrecoId" = NULL '
                            'WHERE "TabelaPrecoId" = %s AND "PessoaId" NOT IN '
                            '(SELECT "Id" FROM "Pessoa" WHERE "Documento" = ANY(%s))',
                            (id_clube, cpfs))
                desligados = cur.rowcount
            else:
                ligados = desligados = 0

        if dry_run:
            sim.rollback()
        else:
            sim.commit()
        log(f"[precos] tabela={NOME_TABELA} ofertas={len(ofertas)} "
            f"precos_alterados={mudancas} clientes_ligados={ligados} "
            f"desligados={desligados}" + (" (DRY-RUN)" if dry_run else ""))
        return mudancas
    except Exception:
        sim.rollback()
        raise
    finally:
        sim.close()
        clube.close()


def main():
    dry = "--dry-run" in sys.argv
    try:
        sincronizar(dry_run=dry)
    except Exception as exc:
        log(f"[precos] ERRO: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
