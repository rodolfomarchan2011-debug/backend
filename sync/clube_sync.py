"""Agente de sincronizacao do Clube Diana (roda na maquina LOCAL).

Dois trabalhos:
  1) push-cadastros : clientes do clube (TiDB)  ->  "Pessoa" + "Cliente" no SIM
  2) pull-compras   : "Venda" do SIM (por CPF)  ->  tabela compras (TiDB do clube)

Seguranca (mesmo padrao do financeiro):
  - SIM leitura: usuario somente leitura (DIANA_SIM_VIVO_DATABASE_URL).
  - SIM escrita: usuario restrito, SOMENTE INSERT em Pessoa/Cliente.
  - Nunca DELETE/TRUNCATE/ALTER. Idempotente (uuid5 por CPF/venda).
  - Dedup por CPF: reaproveita Pessoa/Cliente ja existentes no SIM.
  - Guarda de nome: se o CPF ja existe no SIM com OUTRO primeiro nome,
    NAO vincula (evita juntar duas pessoas e vazar compras). Fica como
    erro para a loja revisar; use --forcar quando for a mesma pessoa.
  - Backup JSON antes de escrever no SIM.
  - Logs apenas com contagens (nunca CPF/nome) no log do agente.

Uso:
  python sync/clube_sync.py --all
  python sync/clube_sync.py --push-cadastros [--dry-run] [--cpf 000.000.000-00] [--forcar]
  python sync/clube_sync.py --pull-compras   [--dry-run] [--dias 90]
  python sync/clube_sync.py --status
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, unquote

import pymysql
import psycopg2
import psycopg2.extras

# --- quando roda sem console (pythonw/Agendador), print() nao tem para onde ir
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATES = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "..", "IA TELEGRAM", ".env"),
    os.path.join(ROOT, "..", "..", "IA TELEGRAM", ".env"),
]
BACKUP_DIR = os.path.join(ROOT, "backups")
LOG_DIR = os.path.join(ROOT, "logs")
LOG_PATH = os.path.join(LOG_DIR, "sync.log")
LOCK_PATH = os.path.join(LOG_DIR, ".sync.lock")
PUSH_LOCK_PATH = os.path.join(LOG_DIR, ".push.lock")
ALERTA_PATH = os.path.join(LOG_DIR, "ALERTA.txt")
ALERTA_PS1 = os.path.join(ROOT, "sync", "alerta.ps1")
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
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if val:
                    values.setdefault(key, val)
    for k, v in os.environ.items():
        if v:
            values[k] = v
    return values


def log(msg):
    """Grava no logs/sync.log e mostra na tela (sem dados pessoais)."""
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(msg)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        if os.path.isfile(LOG_PATH) and os.path.getsize(LOG_PATH) > 2_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(linha + "\n")
    except OSError:
        pass


def pegar_lock(caminho=None, validade=1800):
    """Impede duas execucoes ao mesmo tempo (agendador + clique manual)."""
    caminho = caminho or LOCK_PATH
    os.makedirs(LOG_DIR, exist_ok=True)
    if os.path.isfile(caminho):
        idade = time.time() - os.path.getmtime(caminho)
        if idade < validade:
            log(f"[lock] outra execucao em andamento ({int(idade // 60)} min). Saindo.")
            return False
        log("[lock] lock antigo encontrado, seguindo.")
    with open(caminho, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    return True


def soltar_lock(caminho=None):
    try:
        os.remove(caminho or LOCK_PATH)
    except OSError:
        pass


def registrar_falhas(env, sucesso):
    """Conta falhas tecnicas seguidas (banco/SIM fora, erro inesperado)."""
    try:
        my = mysql_conn(club_url(env))
    except Exception:
        return 0
    try:
        with my.cursor() as cur:
            cur.execute("SELECT valor FROM sync_control WHERE chave='falhas_consecutivas'")
            r = cur.fetchone()
            n = int(r[0]) if r and str(r[0]).isdigit() else 0
            n = 0 if sucesso else n + 1
            cur.execute(
                "INSERT INTO sync_control (chave, valor) VALUES ('falhas_consecutivas', %s) "
                "ON DUPLICATE KEY UPDATE valor=VALUES(valor)", (str(n),))
        return n
    except Exception:
        return 0
    finally:
        my.close()


def alertar_se_preciso(n, detalhe=""):
    """Depois de 3 falhas seguidas: arquivo de alerta + aviso no Windows."""
    if n >= 3:
        texto = (f"{datetime.now():%d/%m/%Y %H:%M} - {n} falhas seguidas na sincronizacao.\n"
                 f"{detalhe}\nAbra o painel da loja: backend\\painel\\painel.bat")
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(ALERTA_PATH, "w", encoding="utf-8") as fh:
                fh.write(texto)
        except OSError:
            pass
        if os.path.isfile(ALERTA_PS1):
            try:
                subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-File", ALERTA_PS1], timeout=20, capture_output=True)
            except Exception:
                pass
    elif os.path.isfile(ALERTA_PATH):
        try:
            os.remove(ALERTA_PATH)
        except OSError:
            pass


def backup_automatico(env, forcar=False):
    """Faz backup do banco do clube a cada 7 dias (ou quando --backup)."""
    ultimo = None
    try:
        my = mysql_conn(club_url(env))
        try:
            with my.cursor() as cur:
                cur.execute("SELECT valor FROM sync_control WHERE chave='backup_ultimo'")
                r = cur.fetchone()
                ultimo = r[0] if r else None
        finally:
            my.close()
    except Exception:
        return

    if ultimo and not forcar:
        try:
            if datetime.now() - datetime.fromisoformat(ultimo) < timedelta(days=7):
                return
        except ValueError:
            pass

    script = os.path.join(ROOT, "scripts", "backup_clube.py")
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                           timeout=240, encoding="utf-8", errors="ignore")
        linhas = (r.stdout or r.stderr or "").strip().splitlines()
        log("[backup] " + (linhas[-1] if linhas else "concluido"))
    except Exception as exc:
        log(f"[backup] falhou: {type(exc).__name__}")


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
    log("[erro] sem URL de leitura do SIM (DIANA_SIM_VIVO_DATABASE_URL). "
        "Confira o .env da pasta 'IA TELEGRAM'.")
    sys.exit(2)


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


def primeiro_nome(nome):
    partes = [p for p in re.split(r"\s+", (nome or "").strip().upper()) if p]
    return partes[0] if partes else ""


def conflito_de_nome(nome_clube, nome_sim):
    """True quando o CPF do clube aponta para OUTRA pessoa no SIM."""
    a, b = primeiro_nome(nome_clube), primeiro_nome(nome_sim)
    if not a or not b or a == b:
        return False
    # aceita variacao por abreviacao (JOSE / J., MARIA / MARIANA)
    if a.startswith(b) or b.startswith(a) or a[:3] == b[:3]:
        return False
    return True


# ------------------------------------------------------------------
# 1) CLUBE -> SIM  (Pessoa + Cliente)
# ------------------------------------------------------------------
def push_cadastros(env, dry_run, cpf_filtro=None, forcar=False):
    write_url = env.get("DIANA_SIM_ESCRITA_URL")
    if not write_url:
        log("[push] DIANA_SIM_ESCRITA_URL ausente: escrita no SIM desativada.")
        return 1

    # lock curto: o push roda de minuto em minuto e nao pode ficar esperando
    # uma sincronizacao completa (--all) terminar.
    if not pegar_lock(PUSH_LOCK_PATH, 120):
        return 0

    my = mysql_conn(club_url(env))
    pg = pg_conn(write_url, readonly=False)
    pending, write_plan, done, errors = [], [], 0, 0

    sql = ("SELECT id, nome, cpf, telefone, data_nascimento, sim_pessoa_id, sim_cliente_id "
           "FROM clientes "
           "WHERE sim_cliente_id IS NULL AND sync_tentativas < %s "
           "AND (sync_status = 'pendente' OR sync_status = 'erro') "
           "AND excluido_em IS NULL")
    params = [MAX_TENTATIVAS]
    if cpf_filtro:
        sql += " AND cpf = %s"
        params.append(cpf_filtro)
    sql += " ORDER BY criado_em LIMIT 50"

    try:
        with my.cursor() as cur:
            cur.execute(sql, tuple(params))
            pending = cur.fetchall()

        if not pending:
            log("[push] nenhum cadastro pendente.")
            return 0

        with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as pc:
            for row in pending:
                cid, nome, cpf_raw, tel, nasc, sim_pessoa, sim_cli = row
                cpf = only_digits(cpf_raw)
                if len(cpf) != 11:
                    if not dry_run:
                        _mark_error(my, cid, "CPF invalido")
                    errors += 1
                    continue
                if sim_pessoa and sim_cli:
                    _mark_ok(my, cid, sim_pessoa, sim_cli)
                    done += 1
                    continue

                # dedup por CPF
                pc.execute(
                    "SELECT \"Id\",\"Nome\" FROM \"Pessoa\" WHERE "
                    "regexp_replace(COALESCE(\"Documento\",''),'\\D','','g') = %s LIMIT 1",
                    (cpf,))
                found = pc.fetchone()

                if found and not forcar and conflito_de_nome(nome, found["Nome"]):
                    msg = ("CPF ja cadastrado no SIM como '%s' (nome do clube: '%s'). "
                           "Revisar: e a mesma pessoa? Se sim rode com --forcar."
                           % (primeiro_nome(found["Nome"]), primeiro_nome(nome)))
                    if dry_run:
                        log("[push][dry-run] " + msg)
                    else:
                        _mark_error(my, cid, msg, tentar=False)
                    errors += 1
                    continue

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
                    done += 1
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
            log("[push] backup: " + backup(write_plan))
        log(f"[push] pendentes={len(pending)} ok={done} erros={errors} "
            f"{'(DRY-RUN, nada gravado)' if dry_run else ''}")
        return errors
    finally:
        pg.close()
        my.close()
        soltar_lock(PUSH_LOCK_PATH)


def _mark_ok(my, cid, pessoa_id, cliente_id):
    with my.cursor() as cur:
        cur.execute(
            "UPDATE clientes SET sim_pessoa_id=%s, sim_cliente_id=%s, "
            "sync_status='sincronizado', sync_erro=NULL, last_synced_at=NOW() WHERE id=%s",
            (pessoa_id, cliente_id, cid))


def _mark_error(my, cid, msg, tentar=True):
    with my.cursor() as cur:
        cur.execute(
            "UPDATE clientes SET sync_status='erro', sync_erro=%s, "
            "sync_tentativas=sync_tentativas+%s WHERE id=%s",
            (msg[:500], 1 if tentar else 0, cid))


# ------------------------------------------------------------------
# 2) SIM -> CLUBE  (compras por CPF)
# ------------------------------------------------------------------
def reconciliar_vendas(my, pg, dias=365):
    """Tira do clube as compras cuja venda nao existe mais no SIM.

    No SIM, venda cancelada e APAGADA da tabela "Venda" (nao ha coluna de
    status). Sem esta conferencia, a compra cancelada ficaria para sempre no
    painel do cliente, contando na economia dele.
    """
    try:
        with my.cursor() as cur:
            cur.execute("SELECT id, sim_venda_id FROM compras "
                        "WHERE data_venda >= DATE_SUB(NOW(), INTERVAL %s DAY)",
                        (int(dias),))
            locais = cur.fetchall()
    except Exception as exc:
        log(f"[pull] aviso: nao consegui ler as compras do clube ({type(exc).__name__}).")
        return 0
    if not locais:
        return 0

    ids = [str(linha[1]) for linha in locais]
    existentes = set()
    try:
        with pg.cursor() as pc:
            for i in range(0, len(ids), 200):
                pc.execute('SELECT "Id"::text FROM "Venda" WHERE "Id"::text = ANY(%s)',
                           (ids[i:i + 200],))
                existentes.update(r[0] for r in pc.fetchall())
    except Exception as exc:
        # Se a conferencia falhar, nao apaga nada (melhor manter do que perder
        # compra boa por causa de um timeout).
        log(f"[pull] aviso: nao consegui conferir as vendas no SIM "
            f"({type(exc).__name__}); nada foi removido.")
        return 0

    sumiram = [linha[0] for linha in locais if str(linha[1]) not in existentes]
    if not sumiram:
        return 0

    with my.cursor() as cur:
        for i in range(0, len(sumiram), 200):
            cur.executemany("DELETE FROM compras WHERE id=%s",
                            [(x,) for x in sumiram[i:i + 200]])
    log(f"[pull] {len(sumiram)} compra(s) cancelada(s) no SIM sairam do clube.")
    return len(sumiram)


def reconciliar_clientes(my, pg):
    """Cliente apagado no PDV (SIM) tambem sai do clube.

    Regra de seguranca: so apaga a conta do clube quando ela NAO tem compras
    registradas. Com compras, o cadastro fica (historico) e passa para
    'ignorado' com o motivo, aparecendo no painel da loja.
    """
    try:
        with my.cursor() as cur:
            cur.execute("SELECT id, nome, sim_cliente_id FROM clientes "
                        "WHERE excluido_em IS NULL AND sim_cliente_id IS NOT NULL")
            vinculados = cur.fetchall()
    except Exception as exc:
        log(f"[pull] aviso: nao consegui ler os clientes do clube ({type(exc).__name__}).")
        return 0
    if not vinculados:
        return 0

    ids = [str(linha[2]) for linha in vinculados]
    existentes = set()
    try:
        with pg.cursor() as pc:
            for i in range(0, len(ids), 200):
                pc.execute('SELECT "Id"::text FROM "Cliente" WHERE "Id"::text = ANY(%s)',
                           (ids[i:i + 200],))
                existentes.update(r[0] for r in pc.fetchall())
    except Exception as exc:
        # SIM fora do ar: nao mexe em cadastro nenhum.
        log(f"[pull] aviso: nao consegui conferir os clientes no SIM "
            f"({type(exc).__name__}); nada foi alterado.")
        return 0

    apagados = 0
    mantidos = 0
    with my.cursor() as cur:
        for cid, nome, sim_id in vinculados:
            if str(sim_id) in existentes:
                continue
            cur.execute("SELECT COUNT(*) FROM compras WHERE cliente_id=%s", (cid,))
            tem_compra = int(cur.fetchone()[0]) > 0
            if tem_compra:
                cur.execute(
                    "UPDATE clientes SET sim_pessoa_id=NULL, sim_cliente_id=NULL, "
                    "sync_status='ignorado', "
                    "sync_erro='Pessoa apagada no PDV (SIM). Cadastro mantido porque tem compras.' "
                    "WHERE id=%s", (cid,))
                mantidos += 1
            else:
                cur.execute("DELETE FROM clientes WHERE id=%s", (cid,))
                apagados += 1

    if apagados or mantidos:
        log(f"[pull] {apagados} cliente(s) apagado(s) no SIM sairam do clube"
            + (f"; {mantidos} mantido(s) por ter compras." if mantidos else "."))
    return apagados + mantidos


def pull_compras(env, dry_run, dias):
    read_url = sim_read_url(env)
    my = mysql_conn(club_url(env))
    pg = pg_conn(read_url, readonly=True)
    inserted = 0

    try:
        # Cliente apagado no PDV sai do clube (ou vira 'ignorado' se tiver compras).
        if not dry_run:
            reconciliar_clientes(my, pg)

        with my.cursor() as cur:
            cur.execute("SELECT cpf, id FROM clientes "
                       "WHERE excluido_em IS NULL AND sim_cliente_id IS NOT NULL")
            cpf_map = {only_digits(cpf): cid for cpf, cid in cur.fetchall() if only_digits(cpf)}
            cur.execute("SELECT valor FROM sync_control WHERE chave='compras_watermark'")
            wm = cur.fetchone()
        if not cpf_map:
            log("[pull] nenhum cliente com CPF no clube.")
            return 0

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
            preco_normal = {}
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

                # preco da tabela normal: referencia para calcular a economia do
                # clube (o SIM grava preco menor, nao desconto).
                pc.execute('SELECT "Id" FROM "TabelaPreco" WHERE "NomeTabela" <> %s '
                           'ORDER BY "FrenteCaixa" DESC, "NomeTabela" LIMIT 1',
                           ("CLUBE DIANA",))
                linha_normal = pc.fetchone()
                if linha_normal:
                    pc.execute('SELECT "ProdutoId"::text AS pid, "PrecoVenda" AS preco '
                               'FROM "ProdutoTabelaPreco" WHERE "TabelaPrecoId" = %s',
                               (linha_normal["Id"],))
                    preco_normal = {l["pid"]: float(l["preco"] or 0) for l in pc.fetchall()}

                # modo da etiqueta de balanca: 0 = total, 1 = peso.
                # Com a etiqueta em TOTAL o SIM deriva o peso do preco, entao o
                # item de balanca NAO teve desconto de verdade: nao somar
                # economia dele (senao o painel do cliente mente).
                pc.execute('SELECT "EtiquetaBalancaTotal" FROM "Empresa"')
                linha_modo = pc.fetchone() or {}
                modo_total = (linha_modo.get("EtiquetaBalancaTotal") or 0) == 0
                balanca_ids = set()
                if modo_total:
                    pc.execute('SELECT "Id"::text AS pid FROM "Produto" '
                               'WHERE "BalancaIntegracao" <> 0')
                    balanca_ids = {l["pid"] for l in pc.fetchall()}

        # Compra cancelada no caixa some do SIM; aqui ela tambem sai do clube.
        removidas = 0
        if not dry_run:
            removidas = reconciliar_vendas(my, pg)

        if not rows:
            log(f"[pull] nenhuma venda nova desde {since:%d/%m/%Y}."
                + (f" removidas={removidas}" if removidas else ""))
            return 0

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

                # economia do clube: preco normal menos o que ele pagou, item a item
                itens = items.get(vid, [])
                economia = 0.0
                for it in itens:
                    normal = preco_normal.get(it["produto_id"])
                    if normal is None:
                        continue
                    it["preco_normal"] = round(normal, 2)
                    if modo_total and it["produto_id"] in balanca_ids:
                        # etiqueta em total: o peso e derivado do preco, nao houve
                        # desconto real neste item
                        continue
                    diferenca = normal - it["preco"]
                    if diferenca > 0:
                        economia += diferenca * it["quantidade"]
                economia = round(economia, 2)

                payload = json.dumps(itens, ensure_ascii=False)
                if dry_run:
                    inserted += 1
                    continue
                cur.execute(
                    "INSERT INTO compras (id, cliente_id, sim_venda_id, sim_lancamento_id, "
                    "data_venda, total_produtos, total_desconto, total_liquido, cupom, "
                    "itens_json, economia_clube) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE cliente_id=VALUES(cliente_id), "
                    "data_venda=VALUES(data_venda), total_produtos=VALUES(total_produtos), "
                    "total_desconto=VALUES(total_desconto), total_liquido=VALUES(total_liquido), "
                    "cupom=VALUES(cupom), itens_json=VALUES(itens_json), "
                    "economia_clube=VALUES(economia_clube), synced_at=NOW()",
                    (str(uuid.uuid5(NS, f"clube:venda:{vid}")), cid, vid,
                     str(r["lanc_id"]) if r["lanc_id"] else None, data_v,
                     r["TotalProdutos"] or 0, r["TotalDesconto"] or 0,
                     r["TotalLiquido"] or 0, r["Cupom"], payload, economia))
                inserted += 1

            if not dry_run:
                cur.execute(
                    "INSERT INTO sync_control (chave, valor) VALUES ('compras_watermark', %s) "
                    "ON DUPLICATE KEY UPDATE valor=VALUES(valor)",
                    (max_dt.isoformat(),))
        log(f"[pull] desde {since:%d/%m/%Y} vendas_clube={inserted} "
            f"{'(DRY-RUN, nada gravado)' if dry_run else ''}"
            + (f" removidas={removidas}" if removidas else ""))
        return 0
    finally:
        pg.close()
        my.close()


# ------------------------------------------------------------------
# 3) STATUS (para a loja ver como esta a fila)
# ------------------------------------------------------------------
def status(env):
    my = mysql_conn(club_url(env))
    try:
        with my.cursor() as cur:
            cur.execute("SELECT sync_status, COUNT(*) AS n FROM clientes GROUP BY sync_status")
            contagem = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute(
                "SELECT nome, cpf, sync_erro, sync_tentativas, criado_em FROM clientes "
                "WHERE sync_status='erro' ORDER BY atualizado_em DESC LIMIT 10")
            erros = cur.fetchall()
            cur.execute(
                "SELECT nome, cpf, last_synced_at FROM clientes "
                "WHERE sync_status='sincronizado' ORDER BY last_synced_at DESC LIMIT 5")
            ok = cur.fetchall()
    finally:
        my.close()

    print("\n=== Fila de sincronizacao do clube ===")
    for chave in ("pendente", "sincronizado", "erro", "ignorado"):
        print(f"  {chave:12s}: {contagem.get(chave, 0)}")

    if ok:
        print("\nUltimos cadastros que chegaram na loja:")
        for nome, cpf, quando in ok:
            print(f"  - {nome[:34]:34s} {fmt_cpf(only_digits(cpf)):16s} "
                  f"{quando:%d/%m/%Y %H:%M}" if quando else "")
    if erros:
        print("\nPrecisam de atencao:")
        for nome, cpf, erro, tent, quando in erros:
            print(f"  - {nome[:34]:34s} {fmt_cpf(only_digits(cpf))}")
            print(f"      {erro} (tentativas: {tent})")

    ultima = ""
    if os.path.isfile(LOG_PATH):
        with open(LOG_PATH, encoding="utf-8", errors="ignore") as fh:
            linhas = fh.read().strip().splitlines()
            ultima = linhas[-1] if linhas else ""
    print("\nUltima execucao:", ultima or "nunca rodou")
    print()


def main():
    ap = argparse.ArgumentParser(description="Sync Clube Diana <-> SIM")
    ap.add_argument("--push-cadastros", action="store_true")
    ap.add_argument("--pull-compras", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--backup", action="store_true", help="faz o backup agora e sai")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cpf", default=None, help="sincroniza so este CPF")
    ap.add_argument("--forcar", action="store_true",
                    help="vincula mesmo se o nome no SIM for diferente")
    ap.add_argument("--dias", type=int, default=int(os.environ.get("COMPRAS_DIAS_INICIAIS", "90")))
    args = ap.parse_args()

    env = load_env()

    if args.status:
        status(env)
        return 0

    if args.backup:
        backup_automatico(env, forcar=True)
        return 0

    if not (args.all or args.push_cadastros or args.pull_compras):
        ap.print_help()
        return 0

    # push sozinho tem lock proprio (curto); --all e --pull usam o global
    somente_push = args.push_cadastros and not args.all and not args.pull_compras
    if somente_push:
        pegou_global = False
    else:
        if not pegar_lock(LOCK_PATH):
            return 0
        pegou_global = True
    try:
        if not args.dry_run:
            backup_automatico(env)

        erros, tecnico = 0, False
        try:
            if args.all or args.push_cadastros:
                erros += push_cadastros(env, args.dry_run,
                                        only_digits(args.cpf) or None, args.forcar)
            if args.all or args.pull_compras:
                erros += pull_compras(env, args.dry_run, args.dias)
        except Exception as exc:
            tecnico = True
            log(f"[erro] {type(exc).__name__}: {str(exc)[:200]}")

        # Precos do clube no SIM (tabela CLUBE DIANA). Falha aqui nao derruba
        # o sync: o cadastro e as compras continuam funcionando.
        if args.all and not args.dry_run:
            try:
                import precos_clube
                precos_clube.sincronizar()
            except Exception as exc:
                log(f"[precos] aviso: {type(exc).__name__}: {str(exc)[:160]}")

        if not args.dry_run:
            alertar_se_preciso(registrar_falhas(env, sucesso=not tecnico),
                               "Confira se o SIM e o banco estao acessiveis.")
        return 1 if tecnico else 0  # erro de cadastro nao e falha tecnica
    finally:
        if pegou_global:
            soltar_lock()


if __name__ == "__main__":
    sys.exit(main())
