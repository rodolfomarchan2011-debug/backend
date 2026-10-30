"""Troca a senha de um cliente do Clube Diana (uso da loja).

Existe porque ainda nao ha envio automatico de e-mail/SMS. O fluxo e:
  1) o cliente pede a recuperacao pelo WhatsApp (botao "Esqueci minha senha");
  2) a loja confere a identidade (CPF + nome/data de nascimento);
  3) a loja roda este script e envia a senha nova pelo WhatsApp.

Uso:
    python scripts/reset_senha.py                 # pede o CPF e gera senha aleatoria
    python scripts/reset_senha.py --cpf 11144477735
    python scripts/reset_senha.py --cpf 11144477735 --senha MinhaSenha123
    python scripts/reset_senha.py --cpf 11144477735 --dry-run

Seguranca: nao apaga nada, so atualiza senha_hash. Nao imprime CPF completo
nem a senha em log de arquivo (apenas na tela, uma vez).
"""
import argparse
import os
import secrets
import string
import sys
from urllib.parse import urlparse, urlunparse, unquote

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATES = [os.path.join(ROOT, ".env")]


def load_env():
    values = {}
    for path in ENV_CANDIDATES:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if v:
                    values.setdefault(k, v)
    for k, v in os.environ.items():
        if v:
            values[k] = v
    return values


def club_url(env):
    if env.get("CLUBE_DATABASE_URL"):
        return env["CLUBE_DATABASE_URL"]
    base = env.get("DIANA_DATABASE_URL")
    if not base:
        sys.exit("Defina CLUBE_DATABASE_URL no .env")
    return urlunparse(urlparse(base)._replace(path="/clube_diana"))


def connect(url):
    p = urlparse(url)
    port = p.port or 3306
    return pymysql.connect(
        host=p.hostname, port=port,
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        database=p.path.lstrip("/") or "clube_diana",
        connect_timeout=15, autocommit=True, charset="utf8mb4",
        ssl={"ssl": {}} if port == 4000 else None,
        cursorclass=pymysql.cursors.DictCursor,
    )


def so_digitos(v):
    return "".join(ch for ch in (v or "") if ch.isdigit())


def mascarar_cpf(cpf):
    return f"***.{cpf[3:6]}.{cpf[6:9]}-**"


def gerar_senha(tamanho=12):
    alfabeto = string.ascii_letters + string.digits
    while True:
        senha = "".join(secrets.choice(alfabeto) for _ in range(tamanho))
        if (any(c.islower() for c in senha) and any(c.isupper() for c in senha)
                and any(c.isdigit() for c in senha)):
            return senha


def main():
    ap = argparse.ArgumentParser(description="Troca a senha de um cliente do Clube Diana")
    ap.add_argument("--cpf", help="CPF do cliente (somente numeros)")
    ap.add_argument("--senha", help="Nova senha (se vazio, gera uma aleatoria)")
    ap.add_argument("--dry-run", action="store_true", help="So mostra o que seria feito")
    args = ap.parse_args()

    cpf = so_digitos(args.cpf or input("CPF do cliente (somente numeros): "))
    if len(cpf) != 11:
        sys.exit("CPF invalido: precisa ter 11 digitos.")

    sys.path.insert(0, os.path.join(ROOT, "api"))
    try:
        from app import hash_senha, cpf_valido  # reaproveita o mesmo algoritmo
    except Exception as exc:
        sys.exit(f"Nao consegui carregar api/app.py: {exc}")

    if not cpf_valido(cpf):
        sys.exit("CPF invalido (digito verificador).")

    senha = args.senha or gerar_senha()
    if len(senha) < 6:
        sys.exit("A senha precisa ter pelo menos 6 caracteres.")

    conn = connect(club_url(load_env()))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nome FROM clientes WHERE cpf=%s", (cpf,))
            row = cur.fetchone()
            if not row:
                sys.exit("Nao existe cliente com esse CPF no clube.")
            print(f"Cliente encontrado: {row['nome']} ({mascarar_cpf(cpf)})")
            if args.dry_run:
                print("DRY-RUN: nada foi alterado.")
                return
            cur.execute("UPDATE clientes SET senha_hash=%s WHERE id=%s",
                        (hash_senha(senha), row["id"]))
            print("Senha atualizada.")
            print("=" * 40)
            print(f"  Nova senha: {senha}")
            print("=" * 40)
            print("Envie essa senha ao cliente pelo WhatsApp e oriente a trocar depois.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
