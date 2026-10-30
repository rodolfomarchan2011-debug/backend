"""Agente de sincronizacao do Clube Diana (roda na maquina LOCAL).

Dois trabalhos:
  1) push-cadastros : clientes do clube (TiDB)  ->  "Pessoa" + "Cliente" no SIM
  2) pull-compras   : "Venda" do SIM (por CPF)  ->  tabela compras (TiDB do clube)

Seguranca (mesmo padrao do financeiro):
  - SIM leitura: usuario somente leitura (DIANA_SIM_VIVO_DATABASE_URL).
  - SIM escrita: usuario restrito, SOMENTE INSERT em Pessoa/Cliente.
  - Nunca DELETE/TRUNCATE/ALTER. Idempotente (uuid5 por CPF/venda).
  - Dedup por CPF: reaproveita Pessoa/Cliente ja existentes no SIM.
  - Backup JSON antes de escrever no SIM.
  - Logs apenas com contagens (nunca CPF/nome).

Uso:
  python sync/clube_sync.py --all
  python sync/clube_sync.py --push-cadastros [--dry-run]
  python sync/clube_sync.py --pull-compras   [--dry-run]
"""
import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, unquote

import pymysql
import psycopg2
import psycopg2.extras

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATES = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "..", "IA TELEGRAM", ".env"),
]
BACKUP_DIR = os.path.join(ROOT, "backups")
MAX_TENTATIVAS = 8
NS = uuid.UUID("6f1c2d3e-4a5b-4c6d-8e7f-90a1b2c3d4e5")


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
                key, val = key.strip(), val.strip()
                if val:
                    values.setdefault(key, val)
    return values


def club_url(env):
    if env.get("CLUBE_DATABASE_URL"):
        return env["CLUBE_DATABASE_URL"]
    base = env.get("DIANA_DATABASE_URL")
    if not base:
        sys.exit("Defina CLUBE_DATABASE_URL no .env")
    return urlunparse(urlparse(base)._replace(path="/clube_diana"))


def sim_read_url(env):
    for key in ("DIANA_SIM_VIVO_DATABASE_URL", "DIANA_SIM_DATABASE_URL", "SIM_LEITURA_URL"):
        if env.get(key):
            return env[key]
    sys.exit("Sem URL de leitura do SIM (DIANA_SIM_VIVO_DATABASE_URL)")


def mysql_conn(url):
    p = urlparse(url)
    return pymysql.connect(
        host=p.hostname, port=p.port or 3306,
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        database=p.path.lstrip("/") or "clube_diana",
        connect_timeout=20, autocommit=True,
        ssl={"ssl": {}} if (p.port or 3306) == 4000 else None,
        charset="utf8mb4",
    )


def pg_conn(url, readonly):
    conn = psycopg2.connect(url, connect_timeout=20,
                            options="-c statement_timeout=30000")
    conn.set_session(readonly=readonly, autocommit=True)
    return conn


def only_digits(value):
    return re.sub(r"\D", "", value or "")


def fmt_cpf(digits):
    if len(digits) != 11:
        return digits
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def backup(records):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, f"clube-sim-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2, default=str)
    return path


# ------------------------------------------------------------------
# 1) CLUBE -> SIM  (Pessoa + Cliente)
# ------------------------------------------------------------------
def push_cadastros(env, dry_run):
    write_url = env.get("DIANA_SIM_ESCRITA_URL")
    if not write_url:
        print("[push] DIANA_SIM_ESCRITA_URL ausente: escrita no SIM desativada.")
        return

    my = mysql_conn(club_url(env))
    pg = pg_conn(write_url, readonly=False)
    pending, write_plan, done, errors = [], [], 0, 0

    try:
        with my.cursor() as cur:
            cur.execute(
                "SELECT id, nome, cpf, telefone, data_nascimento, sim_pessoa_id, sim_cliente_id "
                "FROM clientes "
                "WHERE sim_cliente_id IS NULL AND sync_tentativas < %s "
                "AND (sync_status = 'pendente' OR sync_status = 'erro') "
                "ORDER BY criado_em LIMIT 50", (MAX_TENTATIVAS,))
            pending = cur.fetchall()

        if not pending:
            print("[push] nenhum cadastro pendente.")
            return

        with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as pc:
            for row in pending:
                cid, nome, cpf_raw, tel, nasc, sim_pessoa, sim_cli = row
                cpf = only_digits(cpf_raw)
                if len(cpf) != 11:
                    _mark_error(my, cid, "CPF invalido")
                    errors += 1
                    continue
                if sim_pessoa and sim_cli:
                    _mark_ok(my, cid, sim_pessoa, sim_cli)
                    done += 1
                    continue

                # dedup por CPF
                pc.execute(
                    "SELECT \"Id\" FROM \"Pessoa\" WHERE "
                    "regexp_replace(COALESCE(\"Documento\",''),'\\D','','g') = %s LIMIT 1",
                    (cpf,))
                found = pc.fetchone()
                pessoa_id = str(found["Id"]) if found else str(uuid.uuid5(NS, f"clube:pessoa:{cpf}"))

                pc.execute("SELECT \"Id\" FROM \"Cliente\" WHERE \"PessoaId\" = %s LIMIT 1",
                           (pessoa_id,))
                cli = pc.fetchone()
                cliente_id = str(cli["Id"]) if cli else str(uuid.uuid5(NS, f"clube:cliente:{cpf}"))

                item = {
                    "cpf": cpf, "nome": nome,
                    "pessoa_nova": not bool(found), "pessoa_id": pessoa_id,
                    "cliente_novo": not bool(cli), "cliente_id": cliente_id,
                    "telefone": tel,
                    "data_nascimento": str(nasc) if nasc else None,
                }
                write_plan.append(item)
                if dry_run:
                    continue

                if not found:
                    pc.execute(
                        'INSERT INTO "Pessoa" '
                        '("Id","Nome","Apelido","Regime","Documento","Telefone","DataNascimento") '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                        (pessoa_id, (nome or "").strip() or "CLIENTE CLUBE", "",
                         0, fmt_cpf(cpf), tel, nasc))
                if not cli:
                    pc.execute(
                        'INSERT INTO "Cliente" '
                        '("Id","PessoaId","LimiteCredito","Bloqueado","DataCadastro") '
                        'VALUES (%s,%s,%s,%s,%s)',
                        (cliente_id, pessoa_id, 0, False, datetime.now()))

                _mark_ok(my, cid, pessoa_id, cliente_id)
                done += 1

        if write_plan and not dry_run:
            print("[push] backup:", backup(write_plan))
        print(f"[push] pendentes={len(pending)} ok={done} erros={errors} "
              f"{'(DRY-RUN, nada gravado)' if dry_run else ''}")
    finally:
        pg.close()
        my.close()


def _mark_ok(my, cid, pessoa_id, cliente_id):
    with my.cursor() as cur:
        cur.execute(
            "UPDATE clientes SET sim_pessoa_id=%s, sim_cliente_id=%s, "
            "sync_status='sincronizado', sync_erro=NULL, last_synced_at=NOW() WHERE id=%s",
            (pessoa_id, cliente_id, cid))


def _mark_error(my, cid, msg):
    with my.cursor() as cur:
        cur.execute(
            "UPDATE clientes SET sync_status='erro', sync_erro=%s, "
            "sync_tentativas=sync_tentativas+1 WHERE id=%s", (msg[:500], cid))


# ------------------------------------------------------------------
# 2) SIM -> CLUBE  (compras por CPF)
# ------------------------------------------------------------------
def pull_compras(env, dry_run, dias):
    read_url = sim_read_url(env)
    my = mysql_conn(club_url(env))
    pg = pg_conn(read_url, readonly=True)
    inserted = updated = 0

    try:
        with my.cursor() as cur:
            cur.execute("SELECT cpf, id FROM clientes")
            cpf_map = {only_digits(cpf): cid for cpf, cid in cur.fetchall() if only_digits(cpf)}
            cur.execute("SELECT valor FROM sync_control WHERE chave='compras_watermark'")
            wm = cur.fetchone()
        if not cpf_map:
            print("[pull] nenhum cliente com CPF no clube.")
            return

        since = datetime.now() - timedelta(days=dias)
        if wm and wm[0]:
            try:
                since = datetime.fromisoformat(wm[0]) - timedelta(days=2)
            except ValueError:
                pass

        sql = """
            SELECT v."Id" AS venda_id, v."LancamentoId" AS lanc_id,
                   l."DataLancamento" AS data_venda,
                   v."TotalProdutos", v."TotalDesconto", v."TotalLiquido", v."Cupom",
                   COALESCE(
                     NULLIF(regexp_replace(COALESCE(p."Documento",''),'\\D','','g'),''),
                     NULLIF(regexp_replace(COALESCE(v."Documento",''),'\\D','','g'),'')
                   ) AS cpf
            FROM "Venda" v
            JOIN "Lancamento" l ON l."Id" = v."LancamentoId"
            LEFT JOIN "Cliente" c ON c."Id" = v."ClienteId"
            LEFT JOIN "Pessoa" p ON p."Id" = c."PessoaId"
            WHERE l."DataLancamento" >= %s
              AND COALESCE(
                    NULLIF(regexp_replace(COALESCE(p."Documento",''),'\\D','','g'),''),
                    NULLIF(regexp_replace(COALESCE(v."Documento",''),'\\D','','g'),'')
                  ) = ANY(%s)
            ORDER BY l."DataLancamento"
        """
        with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as pc:
            pc.execute(sql, (since, list(cpf_map.keys())))
            rows = pc.fetchall()

            venda_ids = [r["venda_id"] for r in rows]
            items = {}
            if venda_ids:
                pc.execute(
                    'SELECT "VendaId","ProdutoId","Quantidade","Preco",'
                    '"TotalDesconto","TotalLiquido" FROM "VendaItem" '
                    'WHERE "VendaId"::text = ANY(%s)', ([str(v) for v in venda_ids],))
                for it in pc.fetchall():
                    items.setdefault(str(it["VendaId"]), []).append({
                        "produto_id": str(it["ProdutoId"]),
                        "quantidade": float(it["Quantidade"] or 0),
                        "preco": float(it["Preco"] or 0),
                        "desconto": float(it["TotalDesconto"] or 0),
                        "total": float(it["TotalLiquido"] or 0),
                    })

        if not rows:
            print(f"[pull] nenhuma venda nova desde {since:%d/%m/%Y}.")
            return

        max_dt = since
        with my.cursor() as cur:
            for r in rows:
                cpf = only_digits(r["cpf"])
                cid = cpf_map.get(cpf)
                if not cid:
                    continue
                vid = str(r["venda_id"])
                data_v = r["data_venda"]
                max_dt = max(max_dt, data_v)
                payload = json.dumps(items.get(vid, []), ensure_ascii=False)
                if dry_run:
                    inserted += 1
                    continue
                cur.execute(
                    "INSERT INTO compras (id, cliente_id, sim_venda_id, sim_lancamento_id, "
                    "data_venda, total_produtos, total_desconto, total_liquido, cupom, itens_json) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE cliente_id=VALUES(cliente_id), "
                    "data_venda=VALUES(data_venda), total_produtos=VALUES(total_produtos), "
                    "total_desconto=VALUES(total_desconto), total_liquido=VALUES(total_liquido), "
                    "cupom=VALUES(cupom), itens_json=VALUES(itens_json), synced_at=NOW()",
                    (str(uuid.uuid5(NS, f"clube:venda:{vid}")), cid, vid,
                     str(r["lanc_id"]) if r["lanc_id"] else None, data_v,
                     r["TotalProdutos"] or 0, r["TotalDesconto"] or 0,
                     r["TotalLiquido"] or 0, r["Cupom"], payload))
                inserted += 1

            if not dry_run:
                cur.execute(
                    "INSERT INTO sync_control (chave, valor) VALUES ('compras_watermark', %s) "
                    "ON DUPLICATE KEY UPDATE valor=VALUES(valor)",
                    (max_dt.isoformat(),))
        print(f"[pull] desde {since:%d/%m/%Y} vendas_clube={inserted} "
              f"{'(DRY-RUN, nada gravado)' if dry_run else ''}")
    finally:
        pg.close()
        my.close()


def main():
    ap = argparse.ArgumentParser(description="Sync Clube Diana <-> SIM")
    ap.add_argument("--push-cadastros", action="store_true")
    ap.add_argument("--pull-compras", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dias", type=int, default=int(os.environ.get("COMPRAS_DIAS_INICIAIS", "90")))
    args = ap.parse_args()

    env = load_env()
    if args.all or args.push_cadastros:
        push_cadastros(env, args.dry_run)
    if args.all or args.pull_compras:
        pull_compras(env, args.dry_run, args.dias)
    if not (args.all or args.push_cadastros or args.pull_compras):
        ap.print_help()


if __name__ == "__main__":
    main()
