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
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
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

carregar();
setInterval(carregar, 30000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    porta = int(os.environ.get("PAINEL_PORT", "4200"))
    print(f"Painel da loja em http://127.0.0.1:{porta}")
    uvicorn.run(app, host="127.0.0.1", port=porta, log_level="warning")
