"""API do Clube Diana.

Responsabilidades:
  - cadastro/login do cliente (CPF + senha)
  - listar compras e resumo (total gasto / economizado)
Le SOMENTE o banco do clube (TiDB). Nunca acessa o SIM.
O envio para o SIM e o pull das vendas ficam a cargo de sync/clube_sync.py.
"""
import hashlib
import hmac
import os
import secrets
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse, unquote

import jwt
import pymysql
import pymysql.cursors
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

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
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if v:
                    values.setdefault(k, v)
    for k, v in os.environ.items():
        if v:
            values[k] = v
    return values


ENV = load_env()
JWT_SECRET = ENV.get("CLUBE_JWT_SECRET") or secrets.token_hex(32)
TOKEN_HOURS = int(ENV.get("CLUBE_TOKEN_HOURS", "12"))
ORIGINS = [o.strip() for o in ENV.get(
    "CLUBE_ALLOWED_ORIGINS",
    "https://clube-diana.onrender.com,http://localhost:5500,http://127.0.0.1:5500",
).split(",") if o.strip()]


def db_url():
    if ENV.get("CLUBE_DATABASE_URL"):
        return ENV["CLUBE_DATABASE_URL"]
    base = ENV.get("DIANA_DATABASE_URL")
    if not base:
        sys.exit("Defina CLUBE_DATABASE_URL no .env")
    return urlunparse(urlparse(base)._replace(path="/clube_diana"))


URL = db_url()


def db():
    p = urlparse(URL)
    return pymysql.connect(
        host=p.hostname, port=p.port or 3306,
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        database=p.path.lstrip("/") or "clube_diana",
        connect_timeout=20, autocommit=True, charset="utf8mb4",
        ssl={"ssl": {}} if (p.port or 3306) == 4000 else None,
        cursorclass=pymysql.cursors.DictCursor,
    )


# ---------------- senha (stdlib scrypt) ----------------
def hash_senha(senha: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    dk = hashlib.scrypt(senha.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def verifica_senha(senha: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=len(dk_hex) // 2)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ---------------- CPF ----------------
def so_digitos(v: str) -> str:
    return "".join(ch for ch in (v or "") if ch.isdigit())


def cpf_valido(cpf: str) -> bool:
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for tam in (9, 10):
        soma = sum(int(cpf[i]) * (tam + 1 - i) for i in range(tam))
        dig = (soma * 10) % 11 % 10
        if dig != int(cpf[tam]):
            return False
    return True


def token(cliente_id: str, cpf: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": cliente_id, "cpf": cpf, "exp": exp},
                      JWT_SECRET, algorithm="HS256")


bearer = HTTPBearer(auto_error=False)


def atual(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Nao autenticado")
    try:
        data = jwt.decode(auth.split(" ", 1)[1], JWT_SECRET, algorithms=["HS256"])
        return {"id": data["sub"], "cpf": data.get("cpf")}
    except jwt.PyJWTError:
        raise HTTPException(401, "Sessao invalida ou expirada")


# ---------------- modelos ----------------
class Cadastro(BaseModel):
    nome: str = Field(min_length=2, max_length=160)
    cpf: str
    telefone: str | None = None
    dataNascimento: str | None = None
    senha: str = Field(min_length=6, max_length=100)
    aceite: bool = False


class Login(BaseModel):
    cpf: str
    senha: str


app = FastAPI(title="Clube Diana API", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"],
    allow_headers=["*"], allow_credentials=False,
)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "clube-diana-api"}


@app.post("/api/auth/register")
def register(body: Cadastro):
    cpf = so_digitos(body.cpf)
    if not cpf_valido(cpf):
        raise HTTPException(400, "CPF invalido")

    nasc = None
    if body.dataNascimento:
        try:
            nasc = datetime.strptime(body.dataNascimento[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "Data de nascimento invalida")

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM clientes WHERE cpf=%s", (cpf,))
            if cur.fetchone():
                raise HTTPException(409, "Este CPF ja tem cadastro. Faca login.")
            cid = secrets.token_hex(18)[:36]
            cur.execute(
                "INSERT INTO clientes (id,nome,cpf,telefone,data_nascimento,senha_hash,"
                "aceite_lgpd,sync_status) VALUES (%s,%s,%s,%s,%s,%s,%s,'pendente')",
                (cid, body.nome.strip(), cpf, (body.telefone or "").strip() or None,
                 nasc, hash_senha(body.senha), 1 if body.aceite else 0))
    finally:
        conn.close()
    return {"ok": True, "token": token(cid, cpf),
            "cliente": {"nome": body.nome.strip(), "cpf": cpf}}


@app.post("/api/auth/login")
def login(body: Login):
    cpf = so_digitos(body.cpf)
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,nome,cpf,senha_hash FROM clientes WHERE cpf=%s", (cpf,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not verifica_senha(body.senha, row["senha_hash"]):
        raise HTTPException(401, "CPF ou senha incorretos")
    return {"ok": True, "token": token(row["id"], row["cpf"]),
            "cliente": {"nome": row["nome"], "cpf": row["cpf"]}}


@app.get("/api/me/compras")
def compras(user=Depends(atual), limit: int = 50):
    limit = max(1, min(limit, 200))
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_venda, total_liquido, total_desconto, cupom, itens_json "
                "FROM compras WHERE cliente_id=%s ORDER BY data_venda DESC LIMIT %s",
                (user["id"], limit))
            rows = cur.fetchall()
    finally:
        conn.close()
    for r in rows:
        r["data_venda"] = r["data_venda"].strftime("%d/%m/%Y %H:%M")
        r["total_liquido"] = float(r["total_liquido"] or 0)
        r["total_desconto"] = float(r["total_desconto"] or 0)
    return {"ok": True, "compras": rows}


@app.get("/api/me/resumo")
def resumo(user=Depends(atual)):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS qtd, COALESCE(SUM(total_liquido),0) AS gasto, "
                "COALESCE(SUM(total_desconto),0) AS economia, MAX(data_venda) AS ultima "
                "FROM compras WHERE cliente_id=%s", (user["id"],))
            r = cur.fetchone()
            cur.execute("SELECT nome,cpf FROM clientes WHERE id=%s", (user["id"],))
            c = cur.fetchone() or {}
    finally:
        conn.close()
    return {"ok": True, "cliente": c, "compras": int(r["qtd"]),
            "total_gasto": float(r["gasto"] or 0),
            "total_economizado": float(r["economia"] or 0),
            "ultima_compra": r["ultima"].strftime("%d/%m/%Y") if r["ultima"] else None}
