"""Envia pelo WhatsApp da loja o codigo de redefinicao de senha.

Roda no PC da loja (o mesmo do agente de sync), de minuto em minuto:
  py -3 sync\\envia_codigos.py

Fluxo:
  1. o cliente pede no site -> a API cria um pedido em `recuperacao`
     (sem codigo: so cliente + telefone + validade de 15 min)
  2. este script gera o codigo de 6 digitos, grava apenas o hash
     (scrypt, o mesmo do cadastro) e envia o texto pelo WhatsApp da loja
  3. o cliente digita o codigo no site -> a API confere o hash

O codigo em texto puro nunca existe no servidor: ele nasce aqui e vai
direto para a mensagem. Log nunca mostra o codigo.

Configuracao no .env (backend):
  WHATSAPP_API_URL=http://127.0.0.1:4000
  WHATSAPP_API_EMAIL=...
  WHATSAPP_API_PASSWORD=...
  WHATSAPP_ACCOUNT_ID=num-01
"""
import hashlib
import json
import os
import re
import secrets
import sys
import urllib.request
from datetime import datetime
from urllib.parse import urlparse, unquote

import pymysql

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
LOG_DIR = os.path.join(ROOT, "logs")
LOG_PATH = os.path.join(LOG_DIR, "codigos.log")

TEXTO = ("Clube Diana: seu codigo para redefinir a senha e {codigo}. "
         "Vale por 10 minutos. Se nao foi voce, ignore esta mensagem.")
VALIDADE_CODIGO_MIN = 10


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


def log(msg):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        if os.path.isfile(LOG_PATH) and os.path.getsize(LOG_PATH) > 1_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(linha + "\n")
    except OSError:
        pass
    print(msg)


def hash_codigo(codigo):
    """Mesmo formato do hash_senha da API (scrypt)."""
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    dk = hashlib.scrypt(codigo.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def conn(env):
    p = urlparse(env["CLUBE_DATABASE_URL"])
    return pymysql.connect(
        host=p.hostname, port=p.port or 3306,
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        database=p.path.lstrip("/") or "clube_diana",
        connect_timeout=20, autocommit=True,
        ssl={"ssl": {}} if (p.port or 3306) == 4000 else None,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def api(base, caminho, corpo=None, token=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(base.rstrip("/") + caminho, data=dados,
                                 method="POST" if corpo is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


def telefone_whatsapp(tel):
    d = re.sub(r"\D", "", tel or "")
    if len(d) in (10, 11):
        return "55" + d
    if len(d) in (12, 13) and d.startswith("55"):
        return d
    return ""


def main():
    env = load_env()
    base = env.get("WHATSAPP_API_URL", "http://127.0.0.1:4000")
    email = env.get("WHATSAPP_API_EMAIL")
    senha = env.get("WHATSAPP_API_PASSWORD")
    conta = env.get("WHATSAPP_ACCOUNT_ID", "num-01")
    if not email or not senha:
        log("[codigos] WHATSAPP_API_EMAIL/PASSWORD ausentes no .env — envio desligado.")
        return 0

    db = conn(env)
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, telefone FROM recuperacao "
                "WHERE enviado_em IS NULL AND usado_em IS NULL AND codigo_hash IS NULL "
                "AND expira_em > NOW() ORDER BY criado_em LIMIT 20")
            fila = cur.fetchall()
    finally:
        db.close()

    if not fila:
        return 0

    try:
        token = api(base, "/auth/login", {"email": email, "password": senha})["token"]
    except Exception as exc:
        log(f"[codigos] falha no login da API de WhatsApp: {type(exc).__name__}")
        return 1

    enviados, falhas = 0, 0
    for item in fila:
        destino = telefone_whatsapp(item["telefone"])
        if not destino:
            _fechar(env, item["id"], "telefone invalido")
            falhas += 1
            continue

        codigo = f"{secrets.randbelow(1000000):06d}"
        # guarda o hash antes de enviar: se o envio falhar, o codigo nao vale
        _guardar_codigo(env, item["id"], hash_codigo(codigo))

        try:
            r = api(base, f"/whatsapp/accounts/{conta}/test",
                    {"to": destino, "text": TEXTO.format(codigo=codigo)}, token)
            if not r.get("ok"):
                raise RuntimeError(r.get("error") or "envio recusado")
            _fechar(env, item["id"], None)
            enviados += 1
        except Exception as exc:
            detalhe = getattr(exc, "code", "") or type(exc).__name__
            _fechar(env, item["id"], f"falha no envio ({detalhe})"[:200])
            falhas += 1

    log(f"[codigos] fila={len(fila)} enviados={enviados} falhas={falhas}")
    return 1 if falhas else 0


def _guardar_codigo(env, rid, codigo_hash):
    db = conn(env)
    try:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE recuperacao SET codigo_hash=%s, "
                "expira_em=DATE_ADD(NOW(), INTERVAL %s MINUTE) WHERE id=%s",
                (codigo_hash, VALIDADE_CODIGO_MIN, rid))
    finally:
        db.close()


def _fechar(env, rid, erro):
    db = conn(env)
    try:
        with db.cursor() as cur:
            cur.execute("UPDATE recuperacao SET enviado_em=NOW(), erro_envio=%s WHERE id=%s",
                        (erro, rid))
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
