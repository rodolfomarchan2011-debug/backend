"""Cria o banco clube_diana e as tabelas no TiDB. Idempotente.

Uso:
    python scripts/apply_schema.py

Le CLUBE_DATABASE_URL do .env local (ou deriva de DIANA_DATABASE_URL do
projeto IA TELEGRAM, trocando o banco para clube_diana).
Nunca imprime credenciais.
"""
import os
import sys
from urllib.parse import urlparse, urlunparse, unquote

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATES = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "..", "IA TELEGRAM", ".env"),
]


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
                key, val = line.split("=", 1)
                values.setdefault(key.strip(), val.strip())
    return values


def club_url(env):
    url = env.get("CLUBE_DATABASE_URL")
    if url:
        return url
    base = env.get("DIANA_DATABASE_URL")
    if not base:
        sys.exit("Defina CLUBE_DATABASE_URL (mysql://user:pass@host:port/clube_diana) no .env")
    parsed = urlparse(base)
    return urlunparse(parsed._replace(path="/clube_diana"))


def connect(url):
    parsed = urlparse(url)
    port = parsed.port or 3306
    ssl = {"ssl": {}} if port == 4000 else None
    return pymysql.connect(
        host=parsed.hostname,
        port=port,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        connect_timeout=20,
        autocommit=True,
        ssl=ssl,
    )


def main():
    env = load_env()
    url = club_url(env)
    parsed = urlparse(url)
    print(f"TiDB: {parsed.hostname}:{parsed.port or 3306} banco alvo={parsed.path.lstrip('/') or '(padrao)'}")

    schema_path = os.path.join(ROOT, "sql", "schema.sql")
    with open(schema_path, encoding="utf-8") as fh:
        sql = fh.read()

    clean = "\n".join(
        ln for ln in sql.lstrip("\ufeff").splitlines() if not ln.strip().startswith("--")
    )
    statements = [chunk.strip() for chunk in clean.split(";") if chunk.strip()]

    conn = connect(url)
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES FROM clube_diana")
            tables = [r[0] for r in cur.fetchall()]
        print("Tabelas em clube_diana:", ", ".join(tables))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
