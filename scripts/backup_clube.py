"""Backup do banco do clube para arquivo local (JSON gzip).

Roda pelo agente de sync uma vez por semana (ou na mao):
    py -3 scripts\\backup_clube.py
    py -3 scripts\\backup_clube.py --manter 12

Grava em backend/backups/backup-clube-AAAAMMDD-HHMMSS.json.gz e mantem
apenas os ultimos arquivos (padrao: 8). Nao apaga nada no banco.
"""
import argparse
import gzip
import json
import os
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlparse, unquote

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATES = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "..", "IA TELEGRAM", ".env"),
    os.path.join(ROOT, "..", "..", "IA TELEGRAM", ".env"),
]
BACKUP_DIR = os.path.join(ROOT, "backups")


def load_env():
    vals = {}
    for path in ENV_CANDIDATES:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    vals.setdefault(k.strip(), v)
    return vals


def json_seguro(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, bytes):
        return valor.decode("utf-8", "ignore")
    return str(valor)


def conectar(env):
    p = urlparse(env["CLUBE_DATABASE_URL"])
    return pymysql.connect(
        host=p.hostname, port=p.port or 3306,
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        database=p.path.lstrip("/") or "clube_diana",
        connect_timeout=25, autocommit=True,
        ssl={"ssl": {}} if (p.port or 3306) == 4000 else None,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def main():
    ap = argparse.ArgumentParser(description="Backup do banco do clube")
    ap.add_argument("--manter", type=int, default=8, help="quantos arquivos guardar")
    args = ap.parse_args()

    env = load_env()
    db = conectar(env)
    dados = {"gerado_em": datetime.now().isoformat(timespec="seconds")}
    try:
        with db.cursor() as cur:
            for tabela in ("clientes", "compras", "newsletter", "sync_control"):
                cur.execute("SELECT * FROM " + tabela)
                dados[tabela] = cur.fetchall()
    finally:
        db.close()

    os.makedirs(BACKUP_DIR, exist_ok=True)
    nome = f"backup-clube-{datetime.now():%Y%m%d-%H%M%S}.json.gz"
    caminho = os.path.join(BACKUP_DIR, nome)
    with gzip.open(caminho, "wt", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, default=json_seguro)

    arquivos = sorted(f for f in os.listdir(BACKUP_DIR)
                      if f.startswith("backup-clube") and f.endswith(".json.gz"))
    for velho in arquivos[:-args.manter]:
        try:
            os.remove(os.path.join(BACKUP_DIR, velho))
        except OSError:
            pass

    print(f"[backup] {nome} | clientes={len(dados['clientes'])} "
          f"compras={len(dados['compras'])} newsletter={len(dados['newsletter'])} "
          f"({os.path.getsize(caminho)} bytes)")

    # marca no banco quando foi o ultimo backup
    db = conectar(env)
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO sync_control (chave, valor) VALUES ('backup_ultimo', %s) "
                "ON DUPLICATE KEY UPDATE valor=VALUES(valor)",
                (datetime.now().isoformat(timespec="seconds"),))
    finally:
        db.close()


if __name__ == "__main__":
    main()
