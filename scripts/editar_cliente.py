"""Corrige os dados de um cliente do clube (nome, CPF, telefone).

Uso tipico (CPF digitado errado no cadastro):
    py -3 scripts\\editar_cliente.py --cpf 000.000.000-00 --novo-cpf 111.111.111-11
    py -3 scripts\\editar_cliente.py --cpf 000.000.000-00 --nome "NOME CORRETO"
    py -3 scripts\\editar_cliente.py --cpf 000.000.000-00 --telefone 17999998888
    py -3 scripts\\editar_cliente.py --cpf 000.000.000-00 --reiniciar-sync

Qualquer alteracao rearma a sincronizacao (volta para 'pendente' e zera
as tentativas), para o cadastro ser enviado ao SIM na proxima execucao.
Nunca apaga cliente: so corrige campos.
"""
import argparse
import os
import re
import sys
from urllib.parse import urlparse, unquote

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATES = [os.path.join(ROOT, ".env")]
TAMANHO_NOME = 160


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
                v = v.strip().strip('"').strip("'")
                if v:
                    values.setdefault(k.strip(), v)
    for k, v in os.environ.items():
        if v:
            values[k] = v
    return values


def digits(v):
    return re.sub(r"\D", "", v or "")


def cpf_valido(cpf):
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for tam in (9, 10):
        soma = sum(int(cpf[i]) * (tam + 1 - i) for i in range(tam))
        dig = (soma * 10) % 11 % 10
        if dig != int(cpf[tam]):
            return False
    return True


def fmt(cpf):
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if len(cpf) == 11 else cpf


def main():
    ap = argparse.ArgumentParser(description="Corrige dados de um cliente do clube")
    ap.add_argument("--cpf", required=True, help="CPF atual do cadastro")
    ap.add_argument("--novo-cpf", help="CPF correto (corrige digitacao)")
    ap.add_argument("--nome")
    ap.add_argument("--telefone")
    ap.add_argument("--reiniciar-sync", action="store_true",
                    help="so rearma a sincronizacao com o SIM")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = load_env()
    url = env.get("CLUBE_DATABASE_URL")
    if not url:
        sys.exit("Defina CLUBE_DATABASE_URL no .env")

    cpf = digits(args.cpf)
    if not cpf_valido(cpf):
        sys.exit("CPF informado em --cpf nao e valido.")

    campos, valores = [], []
    if args.novo_cpf:
        novo = digits(args.novo_cpf)
        if not cpf_valido(novo):
            sys.exit("CPF informado em --novo-cpf nao e valido.")
        campos.append("cpf=%s")
        valores.append(novo)
    if args.nome:
        nome = " ".join(args.nome.split())
        if len(nome) < 3:
            sys.exit("Nome muito curto.")
        campos.append("nome=%s")
        valores.append(nome[:TAMANHO_NOME])
    if args.telefone:
        tel = digits(args.telefone)
        if len(tel) not in (10, 11):
            sys.exit("Telefone deve ter 10 ou 11 numeros (com DDD).")
        campos.append("telefone=%s")
        valores.append(tel)

    if not campos and not args.reiniciar_sync:
        sys.exit("Nada a alterar. Use --novo-cpf, --nome, --telefone ou --reiniciar-sync.")

    campos += ["sync_status='pendente'", "sync_erro=NULL", "sync_tentativas=0",
               "sim_pessoa_id=NULL", "sim_cliente_id=NULL", "last_synced_at=NULL"]

    p = urlparse(url)
    conn = pymysql.connect(
        host=p.hostname, port=p.port or 3306,
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        database=p.path.lstrip("/") or "clube_diana",
        connect_timeout=25, autocommit=False,
        ssl={"ssl": {}} if (p.port or 3306) == 4000 else None,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nome, cpf, telefone FROM clientes WHERE cpf=%s", (cpf,))
            row = cur.fetchone()
            if not row:
                sys.exit(f"Nenhum cliente com o CPF {fmt(cpf)}.")
            cid, nome_atual, cpf_atual, tel_atual = row
            print(f"Encontrado: {nome_atual} | CPF {fmt(cpf_atual)} | tel {tel_atual}")

            if args.novo_cpf:
                novo = digits(args.novo_cpf)
                cur.execute("SELECT id FROM clientes WHERE cpf=%s AND id<>%s", (novo, cid))
                if cur.fetchone():
                    sys.exit(f"O CPF {fmt(novo)} ja pertence a outro cadastro.")

            if args.dry_run:
                print("[dry-run] nada foi gravado.")
                return

            cur.execute(f"UPDATE clientes SET {', '.join(campos)} WHERE id=%s",
                        tuple(valores) + (cid,))
        conn.commit()
        print("Atualizado. O cadastro volta para a fila de sincronizacao.")
        if args.novo_cpf:
            print("Rode o agente para enviar ao SIM: py -3 sync\\clube_sync.py --all")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
