"""Painel local da loja do Clube Diana.

Roda no PC da loja (o mesmo do agente de sync) em http://127.0.0.1:4200.
Não é publicado na internet: escuta só no localhost.

Mostra:
  - fila de sincronização (pendente / erro / sincronizado)
  - por que um cadastro não chegou ao PDV
  - última execução do agente e alerta de falha repetida
  - último backup do banco

Permite:
  - rodar a sincronização na hora (sem esperar os 15 minutos)
  - reenviar um cadastro (volta para a fila)
  - corrigir o CPF de um cadastro com erro
"""
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from urllib.parse import urlparse, unquote

import pymysql
import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException
import csv
import io as _io

from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATES = [
    os.path.join(ROOT, ".env"),
    os.path.join(ROOT, "..", "IA TELEGRAM", ".env"),
    os.path.join(ROOT, "..", "..", "IA TELEGRAM", ".env"),
]
LOG_PATH = os.path.join(ROOT, "logs", "sync.log")
BACKUP_DIR = os.path.join(ROOT, "backups")
ALERTA_PATH = os.path.join(ROOT, "logs", "ALERTA.txt")

_rodando = threading.Lock()


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


ENV = load_env()


def conn():
    p = urlparse(ENV["CLUBE_DATABASE_URL"])
    return pymysql.connect(
        host=p.hostname, port=p.port or 3306,
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        database=p.path.lstrip("/") or "clube_diana",
        connect_timeout=15, autocommit=True,
        ssl={"ssl": {}} if (p.port or 3306) == 4000 else None,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def sim_conn():
    """Conexao somente leitura com o PDV (SIM)."""
    url = ENV.get("DIANA_SIM_VIVO_DATABASE_URL") or ENV.get("DIANA_SIM_DATABASE_URL")
    if not url:
        raise HTTPException(503, "SIM nao configurado (DIANA_SIM_VIVO_DATABASE_URL)")
    conn = psycopg2.connect(url, connect_timeout=10, options="-c statement_timeout=20000")
    conn.set_session(readonly=True, autocommit=True)
    return conn


SQL_ADESAO = """
WITH base AS (
  SELECT l."DataLancamento" AS dt,
         COALESCE(
           NULLIF(regexp_replace(COALESCE(p."Documento",''),'\\D','','g'),''),
           NULLIF(regexp_replace(COALESCE(v."Documento",''),'\\D','','g'),'')
         ) AS cpf
  FROM "Venda" v
  JOIN "Lancamento" l ON l."Id" = v."LancamentoId"
  LEFT JOIN "Cliente" c ON c."Id" = v."ClienteId"
  LEFT JOIN "Pessoa" p ON p."Id" = c."PessoaId"
  WHERE l."DataLancamento" >= now() - make_interval(days => %s)
)
SELECT to_char(dt::date, 'DD/MM') AS dia,
       count(*) AS vendas,
       sum(CASE WHEN COALESCE(cpf,'') <> '' THEN 1 ELSE 0 END) AS com_cpf
FROM base
GROUP BY dt::date
ORDER BY dt::date
"""


def cpf_valido(cpf):
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for tam in (9, 10):
        soma = sum(int(cpf[i]) * (tam + 1 - i) for i in range(tam))
        if (soma * 10) % 11 % 10 != int(cpf[tam]):
            return False
    return True


def fmt_cpf(v):
    d = re.sub(r"\D", "", v or "")
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else (v or "")


def ultima_execucao():
    if not os.path.isfile(LOG_PATH):
        return None
    with open(LOG_PATH, encoding="utf-8", errors="ignore") as fh:
        linhas = [l for l in fh.read().splitlines() if l.strip()]
    return linhas[-1] if linhas else None


def backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    arquivos = [f for f in os.listdir(BACKUP_DIR)
                if f.startswith("backup-clube") and f.endswith(".json.gz")]
    arquivos.sort(reverse=True)
    return arquivos


app = FastAPI(title="Painel Clube Diana", docs_url=None, redoc_url=None)


@app.get("/api/adesao")
def adesao(dias: int = 7):
    dias = max(1, min(int(dias), 90))
    try:
        conn = sim_conn()
    except HTTPException as erro:
        return {"ok": False, "erro": erro.detail}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(SQL_ADESAO, (dias,))
            linhas = cur.fetchall()
    except Exception as exc:
        return {"ok": False, "erro": type(exc).__name__}
    finally:
        conn.close()

    total_v = sum(int(r["vendas"]) for r in linhas)
    total_c = sum(int(r["com_cpf"]) for r in linhas)
    return {
        "ok": True,
        "dias": [{"dia": r["dia"], "vendas": int(r["vendas"]), "com_cpf": int(r["com_cpf"])}
                 for r in linhas],
        "total": {"vendas": total_v, "com_cpf": total_c,
                  "pct": round(100.0 * total_c / total_v, 1) if total_v else 0},
    }


SQL_ADESAO_MES = SQL_ADESAO.replace(
    'WHERE l."DataLancamento" >= now() - make_interval(days => %s)',
    'WHERE l."DataLancamento" >= %s AND l."DataLancamento" < %s'
).replace(
    """SELECT to_char(dt::date, 'DD/MM') AS dia,
       count(*) AS vendas,
       sum(CASE WHEN COALESCE(cpf,'') <> '' THEN 1 ELSE 0 END) AS com_cpf
FROM base
GROUP BY dt::date
ORDER BY dt::date""",
    """SELECT count(*) AS vendas,
       sum(CASE WHEN COALESCE(cpf,'') <> '' THEN 1 ELSE 0 END) AS com_cpf
FROM base""")


def _mes(mes):
    """YYYY-MM -> (primeiro dia, primeiro dia do mes seguinte)."""
    try:
        partes = str(mes)[:7].split("-")
        inicio = datetime(int(partes[0]), int(partes[1]), 1)
    except Exception:
        raise HTTPException(400, "Mes invalido")
    if inicio.month == 12:
        fim = datetime(inicio.year + 1, 1, 1)
    else:
        fim = datetime(inicio.year, inicio.month + 1, 1)
    return inicio, fim


@app.get("/api/relatorio")
def relatorio(mes: str | None = None):
    """Numeros do mes: clientes, compras, economia, top clientes e adesao."""
    referencia = mes or datetime.now().strftime("%Y-%m")
    inicio, fim = _mes(referencia)

    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total, SUM(criado_em >= %s) AS novos, "
                        "SUM(sync_status='sincronizado') AS no_pdv "
                        "FROM clientes WHERE excluido_em IS NULL", (inicio,))
            cli = cur.fetchone() or {}
            cur.execute("SELECT COUNT(*) AS compras, COUNT(DISTINCT cliente_id) AS clientes, "
                        "COALESCE(SUM(total_liquido),0) AS valor, "
                        "COALESCE(SUM(total_desconto),0) AS desconto FROM compras "
                        "WHERE data_venda >= %s AND data_venda < %s", (inicio, fim))
            comp = cur.fetchone() or {}
            cur.execute("SELECT c.nome, c.cpf, COUNT(*) AS compras, "
                        "COALESCE(SUM(k.total_liquido),0) AS valor, "
                        "COALESCE(SUM(k.total_desconto),0) AS desconto "
                        "FROM compras k JOIN clientes c ON c.id=k.cliente_id "
                        "WHERE k.data_venda >= %s AND k.data_venda < %s "
                        "GROUP BY c.id ORDER BY valor DESC LIMIT 5", (inicio, fim))
            top = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS n FROM compras")
            historico = int(cur.fetchone()["n"])
    finally:
        db.close()

    adesao = {"vendas": 0, "com_cpf": 0, "pct": 0.0}
    try:
        sim = sim_conn()
        try:
            with sim.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as pc:
                pc.execute(SQL_ADESAO_MES, (inicio, fim))
                linha = pc.fetchone() or {}
            vendas = int(linha.get("vendas") or 0)
            com = int(linha.get("com_cpf") or 0)
            adesao = {"vendas": vendas, "com_cpf": com,
                      "pct": round(100.0 * com / vendas, 1) if vendas else 0.0}
        finally:
            sim.close()
    except Exception:
        adesao["erro"] = "SIM indisponivel"

    return {
        "ok": True,
        "mes": referencia,
        "clientes": {"total": int(cli.get("total") or 0),
                     "novos": int(cli.get("novos") or 0),
                     "no_pdv": int(cli.get("no_pdv") or 0)},
        "compras": {"qtd": int(comp.get("compras") or 0),
                    "clientes": int(comp.get("clientes") or 0),
                    "valor": float(comp.get("valor") or 0),
                    "desconto": float(comp.get("desconto") or 0)},
        "top": [{"nome": r["nome"], "cpf": fmt_cpf(r["cpf"]),
                 "compras": int(r["compras"]), "valor": float(r["valor"]),
                 "desconto": float(r["desconto"])} for r in top],
        "adesao": adesao,
        "compras_historico": historico,
    }


@app.get("/api/exportar")
def exportar(tipo: str = "clientes", de: str | None = None, ate: str | None = None):
    """CSV para o Excel (separador ';' e BOM, abre certo em pt-BR)."""
    tipo = (tipo or "").lower()
    db = conn()
    try:
        with db.cursor() as cur:
            if tipo == "clientes":
                cur.execute("SELECT nome,cpf,telefone,email,data_nascimento,criado_em,"
                            "sync_status,last_synced_at FROM clientes "
                            "WHERE excluido_em IS NULL ORDER BY criado_em")
                colunas = ["nome", "cpf", "telefone", "email", "data_nascimento",
                           "criado_em", "sync_status", "last_synced_at"]
                linhas = cur.fetchall()
            elif tipo == "compras":
                condicoes, params = ["1=1"], []
                if de:
                    condicoes.append("k.data_venda >= %s")
                    params.append(de)
                if ate:
                    condicoes.append("k.data_venda < DATE_ADD(%s, INTERVAL 1 DAY)")
                    params.append(ate)
                cur.execute(
                    "SELECT k.data_venda, c.nome, c.cpf, k.cupom, k.total_produtos, "
                    "k.total_desconto, k.total_liquido FROM compras k "
                    "JOIN clientes c ON c.id=k.cliente_id WHERE " +
                    " AND ".join(condicoes) + " ORDER BY k.data_venda DESC",
                    tuple(params))
                colunas = ["data_venda", "nome", "cpf", "cupom", "total_produtos",
                           "total_desconto", "total_liquido"]
                linhas = cur.fetchall()
            else:
                raise HTTPException(400, "Tipo invalido (use clientes ou compras)")
    finally:
        db.close()

    buf = _io.StringIO()
    escritor = csv.writer(buf, delimiter=";")
    escritor.writerow(colunas)
    for linha in linhas:
        valores = []
        for c in colunas:
            v = linha.get(c)
            if isinstance(v, datetime):
                v = v.strftime("%d/%m/%Y %H:%M")
            valores.append("" if v is None else v)
        escritor.writerow(valores)

    nome = "%s-%s.csv" % (tipo, datetime.now().strftime("%Y%m%d-%H%M"))
    return Response(content=buf.getvalue().encode("utf-8-sig"),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % nome})


class Id(BaseModel):
    id: str


class Correcao(BaseModel):
    id: str
    cpf: str
    nome: str | None = None


@app.get("/api/dados")
def dados():
    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT sync_status, COUNT(*) AS n FROM clientes GROUP BY sync_status")
            contagem = {r["sync_status"]: r["n"] for r in cur.fetchall()}
            cur.execute(
                "SELECT id,nome,cpf,telefone,sync_status,sync_erro,sync_tentativas,"
                "criado_em,last_synced_at,sim_pessoa_id,sim_cliente_id "
                "FROM clientes ORDER BY criado_em DESC LIMIT 200")
            clientes = cur.fetchall()
            cur.execute("SELECT valor FROM sync_control WHERE chave='falhas_consecutivas'")
            r = cur.fetchone()
            falhas = int(r["valor"]) if r and r["valor"] else 0
            cur.execute(
                "SELECT SUM(enviado_em IS NULL AND usado_em IS NULL AND expira_em > NOW()) AS p, "
                "SUM(erro_envio IS NOT NULL AND criado_em > DATE_SUB(NOW(), INTERVAL 1 DAY)) AS f "
                "FROM recuperacao")
            rc = cur.fetchone() or {}
            codigos = {"pendentes": int(rc.get("p") or 0), "falhas": int(rc.get("f") or 0)}
            cur.execute("SELECT COUNT(*) AS n, SUM(descadastrado_em IS NOT NULL) AS d "
                        "FROM newsletter")
            rn = cur.fetchone() or {}
            total_news = int(rn.get("n") or 0)
            saiu_news = int(rn.get("d") or 0)
    finally:
        db.close()

    for c in clientes:
        c["cpf"] = fmt_cpf(c["cpf"])
        c["criado_em"] = c["criado_em"].strftime("%d/%m/%Y %H:%M") if c["criado_em"] else ""
        c["last_synced_at"] = (c["last_synced_at"].strftime("%d/%m/%Y %H:%M")
                               if c["last_synced_at"] else "")
        c["vinculado"] = bool(c["sim_cliente_id"])

    try:
        saida = subprocess.run(
            ["schtasks", "/query", "/tn", "Clube Diana - Sync SIM", "/fo", "LIST"],
            capture_output=True, text=True, timeout=15,
            encoding="oem", errors="ignore").stdout or ""
        proxima = ""
        for linha in saida.splitlines():
            if "xima" in linha and ":" in linha:
                proxima = linha.split(":", 1)[1].strip()
        tarefa = {"existe": "Clube Diana" in saida, "proxima": proxima, "verificado": True}
    except Exception:
        tarefa = {"existe": None, "proxima": "", "verificado": False}

    lista_bkp = backups()
    alerta = None
    if os.path.isfile(ALERTA_PATH):
        with open(ALERTA_PATH, encoding="utf-8", errors="ignore") as fh:
            alerta = fh.read().strip()[:600]

    return {
        "contagem": {
            "pendente": contagem.get("pendente", 0),
            "sincronizado": contagem.get("sincronizado", 0),
            "erro": contagem.get("erro", 0),
            "ignorado": contagem.get("ignorado", 0),
        },
        "clientes": clientes,
        "falhas": falhas,
        "alerta": alerta,
        "ultima_execucao": ultima_execucao(),
        "tarefa": tarefa,
        "backup": {"ultimo": lista_bkp[0] if lista_bkp else None, "total": len(lista_bkp)},
        "newsletter": {"total": total_news, "descadastrados": saiu_news},
        "codigos": codigos,
        "agora": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }


@app.post("/api/reenviar")
def reenviar(body: Id):
    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE clientes SET sync_status='pendente', sync_erro=NULL, "
                "sync_tentativas=0, sim_pessoa_id=NULL, sim_cliente_id=NULL, "
                "last_synced_at=NULL WHERE id=%s", (body.id,))
            if cur.rowcount == 0:
                raise HTTPException(404, "Cadastro nao encontrado")
    finally:
        db.close()
    return {"ok": True, "mensagem": "Cadastro voltou para a fila. Enviado na proxima rodada."}


@app.post("/api/corrigir")
def corrigir(body: Correcao):
    cpf = re.sub(r"\D", "", body.cpf or "")
    if not cpf_valido(cpf):
        raise HTTPException(400, "CPF invalido")
    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT id FROM clientes WHERE cpf=%s AND id<>%s", (cpf, body.id))
            if cur.fetchone():
                raise HTTPException(409, "Esse CPF ja pertence a outro cadastro")
            campos = ("cpf=%s, sync_status='pendente', sync_erro=NULL, "
                      "sync_tentativas=0, sim_pessoa_id=NULL, sim_cliente_id=NULL, "
                      "last_synced_at=NULL")
            valores = [cpf]
            if body.nome:
                campos += ", nome=%s"
                valores.append(" ".join(body.nome.split())[:160])
            cur.execute("UPDATE clientes SET " + campos + " WHERE id=%s",
                        tuple(valores) + (body.id,))
            if cur.rowcount == 0:
                raise HTTPException(404, "Cadastro nao encontrado")
    finally:
        db.close()
    return {"ok": True, "mensagem": "CPF corrigido e cadastro reenviado para a fila."}


@app.post("/api/rodar")
def rodar():
    if not _rodando.acquire(blocking=False):
        return JSONResponse({"ok": False, "mensagem": "Ja existe uma sincronizacao rodando."},
                            status_code=409)
    try:
        script = os.path.join(ROOT, "sync", "clube_sync.py")
        r = subprocess.run([sys.executable, script, "--all"], capture_output=True,
                           text=True, timeout=180, encoding="utf-8", errors="ignore",
                           cwd=ROOT)
        saida = ((r.stdout or "") + (r.stderr or "")).strip()
        return {"ok": r.returncode == 0, "saida": saida[-4000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "saida": "A sincronizacao passou de 3 minutos e foi interrompida."}
    finally:
        _rodando.release()


@app.post("/api/abrir-backups")
def abrir_backups():
    try:
        os.startfile(BACKUP_DIR)  # type: ignore[attr-defined]
        return {"ok": True}
    except Exception:
        return {"ok": False, "mensagem": "Pasta de backups: " + BACKUP_DIR}


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


HTML = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clube Diana - Painel da loja</title>
<style>
  :root{--marrom:#2b1d16;--vermelho:#a4161a;--dourado:#d4a017;--creme:#faf6ef;--linha:#e8ddcd;}
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;background:var(--creme);color:#241a14}
  header{background:var(--marrom);color:#fff;padding:18px 24px;display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between}
  header h1{margin:0;font-size:19px;letter-spacing:.02em}
  header .sub{font-size:12px;opacity:.75;margin-top:2px}
  main{max-width:1080px;margin:0 auto;padding:20px 16px 60px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
  .card{background:#fff;border:1px solid var(--linha);border-radius:12px;padding:14px}
  .card .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#8a7b69;font-weight:700}
  .card .val{font-size:26px;font-weight:700;margin-top:6px}
  .card.erro .val{color:var(--vermelho)}
  .card.ok .val{color:#2e7d32}
  .barra{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
  button{font:inherit;cursor:pointer;border-radius:10px;border:1px solid var(--linha);background:#fff;padding:9px 14px;font-weight:600}
  button:hover{background:#fff7e8}
  button.principal{background:var(--vermelho);border-color:var(--vermelho);color:#fff}
  button.principal:hover{background:#8d1214}
  button.mini{padding:5px 9px;font-size:12px;border-radius:8px}
  .alerta{background:#fdecea;border:1px solid #f5c2c0;color:#8d1214;padding:14px 16px;border-radius:12px;margin-bottom:16px;white-space:pre-wrap}
  table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--linha);border-radius:12px;overflow:hidden}
  th,td{padding:10px 12px;text-align:left;font-size:13px;border-bottom:1px solid var(--linha);vertical-align:top}
  th{background:#f6efe3;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#7a6b59}
  tr:last-child td{border-bottom:none}
  .tag{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:700}
  .tag.pendente{background:#fff3cd;color:#7a5b00}
  .tag.sincronizado{background:#e6f4ea;color:#1e5b2a}
  .tag.erro{background:#fdecea;color:#8d1214}
  .tag.ignorado{background:#eee;color:#555}
  .detalhe{color:#7a6b59;font-size:12px;max-width:340px}
  .acoes{display:flex;gap:6px;flex-wrap:wrap}
  .rodape{margin-top:18px;font-size:12px;color:#8a7b69;display:flex;flex-wrap:wrap;gap:16px}
  pre{background:#2b1d16;color:#f3e9d8;padding:14px;border-radius:12px;overflow:auto;font-size:12px;max-height:320px;white-space:pre-wrap}
  .oculto{display:none}
  input[type=text]{font:inherit;padding:6px 8px;border:1px solid var(--linha);border-radius:8px;width:100%}
  dialog{border:none;border-radius:14px;padding:22px;max-width:420px;width:92%}
  dialog::backdrop{background:rgba(0,0,0,.4)}
  dialog h3{margin-top:0}
  .erro-linha{color:var(--vermelho);font-size:12px;margin-top:6px}
  .secao{font-size:15px;margin:26px 0 4px}
  .dica{font-size:12px;color:#8a7b69;margin:0 0 12px;max-width:720px}
  .adesao{background:#fff;border:1px solid var(--linha);border-radius:12px;padding:14px 16px}
  .adesao .totais{font-size:13px;margin-bottom:10px}
  .adesao .totais b{font-size:20px}
  .barra-dia{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:12px}
  .barra-dia .dia{width:52px;color:#7a6b59}
  .barra-dia .trilho{flex:1;height:10px;background:#f1e7d8;border-radius:99px;overflow:hidden}
  .barra-dia .trilho i{display:block;height:100%;background:#a4161a}
  .barra-dia .trilho i.ok{background:#2e7d32}
  .barra-dia .num{width:130px;text-align:right;color:#7a6b59}
  .mes-input{font:inherit;padding:8px 10px;border:1px solid var(--linha);border-radius:10px;background:#fff}
  .rel-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:14px}
  .rel-item{border:1px solid var(--linha);border-radius:10px;padding:10px 12px;background:#fffdfa}
  .rel-item .lbl{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:#8a7b69;font-weight:700}
  .rel-item .val{font-size:20px;font-weight:700;margin-top:4px}
  .rel-item .val.alerta{color:#a4161a}
  .rel-sub{font-size:12px;color:#8a7b69;margin:0 0 10px}
  table.rel{width:100%;border-collapse:collapse;font-size:13px}
  table.rel th,table.rel td{padding:7px 8px;border-bottom:1px solid var(--linha);text-align:left}
  table.rel th{font-size:10px;text-transform:uppercase;color:#7a6b59}
</style>
</head>
<body>
<header>
  <div>
    <h1>Clube Diana - Painel da loja</h1>
    <div class="sub" id="atualizado">carregando...</div>
  </div>
  <div style="font-size:12px;opacity:.8">roda so neste computador</div>
</header>
<main>
  <div id="alerta" class="alerta oculto"></div>

  <div class="cards">
    <div class="card"><div class="lbl">Pendentes</div><div class="val" id="c-pendente">-</div></div>
    <div class="card erro"><div class="lbl">Com erro</div><div class="val" id="c-erro">-</div></div>
    <div class="card ok"><div class="lbl">No PDV</div><div class="val" id="c-ok">-</div></div>
    <div class="card"><div class="lbl">Newsletter</div><div class="val" id="c-news">-</div></div>
    <div class="card"><div class="lbl">Codigos de senha</div><div class="val" id="c-codigos">-</div></div>
  </div>

  <div class="barra">
    <button class="principal" id="btn-rodar">Sincronizar agora</button>
    <button id="btn-atualizar">Atualizar</button>
    <button id="btn-backups">Abrir backups</button>
  </div>

  <div id="saida" class="oculto"><pre id="saida-txt"></pre></div>

  <table>
    <thead>
      <tr><th>Cliente</th><th>CPF</th><th>Situacao</th><th>Detalhe</th><th>Acoes</th></tr>
    </thead>
    <tbody id="linhas"><tr><td colspan="5">carregando...</td></tr></tbody>
  </table>

  <h2 class="secao">Adesao do CPF no caixa</h2>
  <p class="dica">Vendas em que o caixa identificou o cliente. Se o operador nao escolhe o cliente (ou digita o CPF), a compra nao aparece no painel do cliente.</p>
  <div id="adesao" class="adesao">carregando...</div>

  <h2 class="secao">Relatorio do mes</h2>
  <div class="barra">
    <input type="month" id="rel-mes" class="mes-input">
    <button id="btn-rel">Atualizar</button>
    <button id="btn-csv-clientes">Baixar clientes (CSV)</button>
    <button id="btn-csv-compras">Baixar compras do mes (CSV)</button>
  </div>
  <div id="relatorio" class="adesao">carregando...</div>

  <div class="rodape">
    <span id="f-tarefa">tarefa: -</span>
    <span id="f-ultima">ultima execucao: -</span>
    <span id="f-backup">backup: -</span>
    <span id="f-site">site: clube-diana.onrender.com</span>
  </div>
</main>

<dialog id="dlg">
  <h3>Corrigir CPF</h3>
  <p id="dlg-quem" style="font-size:13px;color:#6b6356"></p>
  <label style="font-size:12px;font-weight:700">CPF correto</label>
  <input type="text" id="dlg-cpf" placeholder="000.000.000-00" maxlength="14" style="margin-top:6px">
  <div class="erro-linha" id="dlg-erro"></div>
  <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
    <button id="dlg-cancelar">Cancelar</button>
    <button class="principal" id="dlg-salvar">Salvar e reenviar</button>
  </div>
</dialog>

<script>
var atual = null;
var $ = function (id) { return document.getElementById(id); };

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}

function carregar() {
  fetch("/api/dados").then(function (r) { return r.json(); }).then(function (d) {
    $("c-pendente").textContent = d.contagem.pendente;
    $("c-erro").textContent = d.contagem.erro;
    $("c-ok").textContent = d.contagem.sincronizado;
    $("c-news").textContent = d.newsletter.total;
    $("c-codigos").textContent = d.codigos ? (d.codigos.pendentes + " / " + d.codigos.falhas) : "-";
    $("atualizado").textContent = "atualizado em " + d.agora;

    var a = $("alerta");
    if (d.alerta) {
      a.classList.remove("oculto");
      a.textContent = "ATENCAO: a sincronizacao falhou varias vezes seguidas.\\n" + d.alerta;
    } else {
      a.classList.add("oculto");
    }

    $("f-tarefa").textContent = "tarefa: " + (d.tarefa.existe === null
      ? "nao foi possivel verificar"
      : (d.tarefa.existe ? ("ativa, proxima " + (d.tarefa.proxima || "-")) : "NAO encontrada"));
    $("f-ultima").textContent = "ultima execucao: " + (d.ultima_execucao || "nunca");
    $("f-backup").textContent = "backup: " + (d.backup.ultimo || "nenhum ainda");

    var t = $("linhas");
    if (!d.clientes.length) {
      t.innerHTML = "<tr><td colspan='5'>Nenhum cadastro ainda.</td></tr>";
      return;
    }
    t.innerHTML = d.clientes.map(function (c) {
      var det = c.sync_erro ? esc(c.sync_erro)
        : (c.vinculado ? "vinculado ao PDV"
        : (c.sync_status === "pendente" ? "aguardando envio" : ""));
      return "<tr>" +
        "<td><strong>" + esc(c.nome) + "</strong><br><span style='font-size:11px;color:#8a7b69'>" +
          esc(c.criado_em) + "</span></td>" +
        "<td>" + esc(c.cpf) + "</td>" +
        "<td><span class='tag " + esc(c.sync_status) + "'>" + esc(c.sync_status) + "</span>" +
          (c.sync_tentativas ? "<br><span style='font-size:11px;color:#8a7b69'>tentativas: " +
            c.sync_tentativas + "</span>" : "") + "</td>" +
        "<td class='detalhe'>" + det + "</td>" +
        "<td><div class='acoes'>" +
          "<button class='mini' data-acao='reenviar' data-id='" + esc(c.id) + "'>Reenviar</button>" +
          "<button class='mini' data-acao='cpf' data-id='" + esc(c.id) +
            "' data-nome='" + esc(c.nome) + "' data-cpf='" + esc(c.cpf) + "'>Corrigir CPF</button>" +
        "</div></td>" +
      "</tr>";
    }).join("");
  }).catch(function (e) {
    $("atualizado").textContent = "sem conexao com o banco: " + e;
  });
}

$("linhas").addEventListener("click", function (ev) {
  var b = ev.target.closest("button[data-acao]");
  if (!b) return;
  if (b.dataset.acao === "reenviar") reenviar(b.dataset.id);
  else abrirCpf(b.dataset.id, b.dataset.nome, b.dataset.cpf);
});

function reenviar(id) {
  fetch("/api/reenviar", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: id })
  }).then(function (r) { return r.json(); }).then(function (d) {
    alert(d.mensagem || d.detail || "ok");
    carregar();
  });
}

function abrirCpf(id, nome, cpf) {
  atual = id;
  $("dlg-quem").textContent = nome + " - CPF atual: " + cpf;
  $("dlg-cpf").value = "";
  $("dlg-erro").textContent = "";
  $("dlg").showModal();
  $("dlg-cpf").focus();
}

$("dlg-cancelar").addEventListener("click", function () { $("dlg").close(); });

$("dlg-salvar").addEventListener("click", function () {
  fetch("/api/corrigir", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: atual, cpf: $("dlg-cpf").value })
  }).then(function (r) {
    return r.json().then(function (d) { return { ok: r.ok, d: d }; });
  }).then(function (x) {
    if (!x.ok) { $("dlg-erro").textContent = x.d.detail || "nao foi possivel salvar"; return; }
    $("dlg").close();
    alert(x.d.mensagem);
    carregar();
  });
});

$("btn-rodar").addEventListener("click", function () {
  var b = $("btn-rodar");
  b.disabled = true;
  b.textContent = "Sincronizando...";
  fetch("/api/rodar", { method: "POST" }).then(function (r) { return r.json(); }).then(function (d) {
    $("saida").classList.remove("oculto");
    $("saida-txt").textContent = d.saida || d.mensagem || "";
    carregar();
  }).catch(function (e) {
    $("saida").classList.remove("oculto");
    $("saida-txt").textContent = "erro: " + e;
  }).then(function () {
    b.disabled = false;
    b.textContent = "Sincronizar agora";
  });
});

$("btn-atualizar").addEventListener("click", carregar);

$("btn-backups").addEventListener("click", function () {
  fetch("/api/abrir-backups", { method: "POST" }).then(function (r) { return r.json(); })
    .then(function (d) { if (!d.ok) alert(d.mensagem); });
});

function carregarAdesao() {
  fetch("/api/adesao?dias=7").then(function (r) { return r.json(); }).then(function (d) {
    var alvo = document.getElementById("adesao");
    if (!d.ok) { alvo.textContent = "Nao foi possivel ler o PDV agora (" + (d.erro || "erro") + ")."; return; }
    var pct = d.total.pct;
    var cor = pct >= 70 ? "ok" : "";
    var html = "<div class='totais'>Ultimos 7 dias: <b>" + pct + "%</b> das vendas com CPF " +
      "(" + d.total.com_cpf + " de " + d.total.vendas + ")</div>";
    d.dias.forEach(function (dia) {
      var p = dia.vendas ? Math.round(1000 * dia.com_cpf / dia.vendas) / 10 : 0;
      html += "<div class='barra-dia'><span class='dia'>" + dia.dia + "</span>" +
        "<div class='trilho'><i class='" + (p >= 70 ? "ok" : "") + "' style='width:" + p + "%'></i></div>" +
        "<span class='num'>" + p + "% (" + dia.com_cpf + "/" + dia.vendas + ")</span></div>";
    });
    alvo.innerHTML = html;
  }).catch(function () {
    document.getElementById("adesao").textContent = "Sem conexao com o PDV.";
  });
}

function dinheiro(v) {
  return "R$ " + (v || 0).toFixed(2).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

function carregarRelatorio() {
  var campo = document.getElementById("rel-mes");
  var mes = campo && campo.value ? campo.value : new Date().toISOString().slice(0, 7);
  if (campo && !campo.value) campo.value = mes;
  fetch("/api/relatorio?mes=" + mes).then(function (r) { return r.json(); }).then(function (d) {
    var alvo = document.getElementById("relatorio");
    if (!d.ok) { alvo.textContent = "Nao foi possivel carregar."; return; }
    var a = d.adesao || {};
    var html = "<div class='rel-grid'>" +
      "<div class='rel-item'><div class='lbl'>Clientes</div><div class='val'>" + d.clientes.total +
        "</div><div class='rel-sub'>" + d.clientes.novos + " novos no mes</div></div>" +
      "<div class='rel-item'><div class='lbl'>No PDV</div><div class='val'>" + d.clientes.no_pdv +
        "</div><div class='rel-sub'>vinculados ao SIM</div></div>" +
      "<div class='rel-item'><div class='lbl'>Compras no mes</div><div class='val'>" + d.compras.qtd +
        "</div><div class='rel-sub'>" + d.compras.clientes + " clientes compraram</div></div>" +
      "<div class='rel-item'><div class='lbl'>Economia gerada</div><div class='val'>" + dinheiro(d.compras.desconto) +
        "</div><div class='rel-sub'>total vendido " + dinheiro(d.compras.valor) + "</div></div>" +
      "<div class='rel-item'><div class='lbl'>Adesao do CPF</div><div class='val" +
        ((a.pct || 0) < 30 ? " alerta" : "") + "'>" + (a.pct || 0) + "%</div><div class='rel-sub'>" +
        (a.vendas || 0) + " vendas no mes</div></div>" +
      "</div>";

    if (d.top && d.top.length) {
      html += "<table class='rel'><thead><tr><th>Cliente</th><th>CPF</th><th>Compras</th>" +
        "<th>Gasto</th><th>Economia</th></tr></thead><tbody>";
      d.top.forEach(function (c) {
        html += "<tr><td>" + c.nome + "</td><td>" + c.cpf + "</td><td>" + c.compras +
          "</td><td>" + dinheiro(c.valor) + "</td><td>" + dinheiro(c.desconto) + "</td></tr>";
      });
      html += "</tbody></table>";
    } else {
      html += "<p class='rel-sub'>Nenhuma compra vinculada neste mes.</p>";
    }
    alvo.innerHTML = html;
  }).catch(function () {
    document.getElementById("relatorio").textContent = "Sem conexao com o banco.";
  });
}

document.getElementById("btn-rel").addEventListener("click", carregarRelatorio);
document.getElementById("rel-mes").addEventListener("change", carregarRelatorio);

function baixarCsv(tipo) {
  var mes = document.getElementById("rel-mes").value || new Date().toISOString().slice(0, 7);
  var url = "/api/exportar?tipo=" + tipo;
  if (tipo === "compras") {
    var partes = mes.split("-");
    var ano = parseInt(partes[0], 10), m = parseInt(partes[1], 10);
    var ultimo = new Date(ano, m, 0).getDate();
    url += "&de=" + mes + "-01&ate=" + mes + "-" + ("0" + ultimo).slice(-2);
  }
  window.location.href = url;
}

document.getElementById("btn-csv-clientes").addEventListener("click", function () { baixarCsv("clientes"); });
document.getElementById("btn-csv-compras").addEventListener("click", function () { baixarCsv("compras"); });

carregarRelatorio();
carregar();
carregarAdesao();
setInterval(carregar, 30000);
setInterval(carregarAdesao, 60000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    porta = int(os.environ.get("PAINEL_PORT", "4200"))
    print(f"Painel da loja em http://127.0.0.1:{porta}")
    uvicorn.run(app, host="127.0.0.1", port=porta, log_level="warning")
