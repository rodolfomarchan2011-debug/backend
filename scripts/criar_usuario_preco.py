"""Cria o usuario de banco que o clube usa para mexer SO em preco.

Permissoes (o minimo necessario):
  SELECT  em Produto, ProdutoTabelaPreco, TabelaPreco, Cliente, Pessoa
  INSERT/UPDATE/DELETE em ProdutoTabelaPreco
  INSERT em TabelaPreco
  UPDATE apenas da coluna TabelaPrecoId em Cliente

Uso (precisa da senha do postgres, uma vez):
    py -3 scripts\\criar_usuario_preco.py

Le SIM_ADMIN_URL do .env, cria/atualiza o usuario clube_preco, grava
SIM_PRECO_URL no .env e NUNCA imprime a senha.
"""
import os
import secrets
import sys
from urllib.parse import urlparse, unquote, quote

import psycopg2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATOS = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "..", "IA TELEGRAM", ".env"),
    os.path.join(ROOT, "..", "..", "IA TELEGRAM", ".env"),
]
USUARIO = "clube_preco"


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


def gravar_env(chave, valor):
    caminho = os.path.join(ROOT, ".env")
    linhas = []
    if os.path.isfile(caminho):
        with open(caminho, encoding="utf-8", errors="ignore") as fh:
            linhas = fh.read().splitlines()
    linhas = [l for l in linhas if not l.startswith(chave + "=")]
    linhas.append(f"{chave}={valor}")
    with open(caminho, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(linhas) + "\n")


def main():
    env = carregar_env()
    url_admin = env.get("SIM_ADMIN_URL")
    if not url_admin:
        sys.exit("Defina SIM_ADMIN_URL no backend\\.env (senha do postgres) para criar o usuario.")

    senha = "clube-" + secrets.token_urlsafe(18)
    conn = psycopg2.connect(url_admin, connect_timeout=15)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (USUARIO,))
        if cur.fetchone():
            cur.execute(f'ALTER ROLE "{USUARIO}" WITH LOGIN PASSWORD %s', (senha,))
            print(f"usuario {USUARIO} ja existia: senha atualizada")
        else:
            cur.execute(f'CREATE ROLE "{USUARIO}" WITH LOGIN PASSWORD %s', (senha,))
            print(f"usuario {USUARIO} criado")

        cur.execute(f'GRANT CONNECT ON DATABASE "SIM" TO "{USUARIO}"')
        cur.execute(f'GRANT USAGE ON SCHEMA public TO "{USUARIO}"')
        cur.execute(f'GRANT SELECT ON "Produto", "ProdutoTabelaPreco", "TabelaPreco", '
                    f'"Cliente", "Pessoa" TO "{USUARIO}"')
        cur.execute(f'GRANT INSERT, UPDATE, DELETE ON "ProdutoTabelaPreco" TO "{USUARIO}"')
        cur.execute(f'GRANT INSERT ON "TabelaPreco" TO "{USUARIO}"')
        cur.execute(f'GRANT UPDATE ("TabelaPrecoId") ON "Cliente" TO "{USUARIO}"')
    conn.close()

    p = urlparse(url_admin)
    usuario_url = quote(USUARIO, safe="")
    senha_url = quote(senha, safe="")
    porta = p.port or 5432
    nova = f"postgresql://{usuario_url}:{senha_url}@{p.hostname}:{porta}/SIM"
    gravar_env("SIM_PRECO_URL", nova)
    print("SIM_PRECO_URL gravada no backend\\.env")

    # prova que funciona: abre conexao e confere as permissoes
    teste = psycopg2.connect(nova, connect_timeout=10)
    teste.set_session(readonly=True, autocommit=True)
    with teste.cursor() as cur:
        cur.execute("SELECT current_user")
        quem = cur.fetchone()[0]
        for tabela, privilegio in (("ProdutoTabelaPreco", "INSERT"),
                                   ("ProdutoTabelaPreco", "UPDATE"),
                                   ("TabelaPreco", "INSERT")):
            nome = '"' + tabela + '"'
            cur.execute("SELECT has_table_privilege(current_user, %s, %s)", (nome, privilegio))
            print(f"  {tabela} {privilegio}: {cur.fetchone()[0]}")
        cur.execute("SELECT has_column_privilege(current_user, '\"Cliente\"'::regclass, "
                    "'TabelaPrecoId', 'UPDATE')")
        print("  Cliente.TabelaPrecoId UPDATE:", cur.fetchone()[0])
    teste.close()
    print(f"conexao de teste OK como {quem}")


if __name__ == "__main__":
    main()
