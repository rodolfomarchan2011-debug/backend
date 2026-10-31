"""API do Clube Diana.

Responsabilidades:
  - cadastro/login do cliente (CPF + senha)
  - listar compras e resumo (total gasto / economizado)
  - LGPD: exportar os dados do titular (art. 18, II) e excluir a conta
  - newsletter do site (antes ficava so no localStorage do navegador)
Le SOMENTE o banco do clube (TiDB). Nunca acessa o SIM.

Fase 1 (endurecimento):
  - rate limit em /api/auth/register e /api/auth/login
  - /docs, /redoc e /openapi.json desligados por padrao (CLUBE_DOCS=1 liga)
  - headers de seguranca + Cache-Control: no-store nas respostas
  - CORS restrito a GET/POST/OPTIONS e aos headers usados
  - pool de conexoes MySQL (sem abrir conexao por requisicao)
  - /api/health valida o banco de verdade (503 se cair)
  - cadastro: aceite obrigatorio, data de nascimento plausivel,
    corrida de CPF duplicado devolve 409 (nao 500)
  - token sem CPF no payload (menos dado pessoal exposto)

Fase 2 (LGPD de verdade + consentimento + newsletter):
  - consentimento gravado com data e versao do texto aceito
  - /api/me/dados  : exporta tudo que o clube guarda do titular
  - /api/me/excluir: anonimiza a conta (nome, CPF, telefone, e-mail,
    data de nascimento e senha). Compras ficam sem dado pessoal
    (obrigacao fiscal) e a conta nao loga mais.
  - /api/newsletter: grava o e-mail no banco (nao mais no navegador)
"""
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import sys
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse, unquote

import jwt
import pymysql
import pymysql.cursors
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_CANDIDATES = [os.path.join(ROOT, ".env")]

# Versao do texto de consentimento (politica de privacidade) aceito no cadastro.
# Trocar a data quando o texto mudar de forma relevante.
VERSAO_POLITICA = os.environ.get("CLUBE_POLITICA_VERSAO", "2026-10-31")


def load_env():
    """Le o .env local e, por cima, as variaveis de ambiente do Render."""
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

JWT_SECRET = ENV.get("CLUBE_JWT_SECRET")
if not JWT_SECRET:
    # Nao derruba a API, mas avisa alto: sem segredo fixo toda sessao cai a
    # cada reinicio/deploy.
    JWT_SECRET = secrets.token_hex(32)
    print("[clube][aviso] CLUBE_JWT_SECRET ausente: usando segredo temporario "
          "(sessoes invalidam a cada deploy).", file=sys.stderr)

TOKEN_HOURS = int(ENV.get("CLUBE_TOKEN_HOURS", "12"))
DOCS_ATIVO = ENV.get("CLUBE_DOCS", "0").strip().lower() in ("1", "true", "sim")
DB_POOL = max(2, int(ENV.get("CLUBE_DB_POOL", "8")))
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


# ---------------- pool de conexoes ----------------
def _connect():
    p = urlparse(URL)
    return pymysql.connect(
        host=p.hostname, port=p.port or 3306,
        user=unquote(p.username or ""), password=unquote(p.password or ""),
        database=p.path.lstrip("/") or "clube_diana",
        connect_timeout=10, read_timeout=20, write_timeout=20,
        autocommit=True, charset="utf8mb4",
        ssl={"ssl": {}} if (p.port or 3306) == 4000 else None,
        cursorclass=pymysql.cursors.DictCursor,
    )


class _PooledConn:
    """Conexao emprestada do pool: close() devolve em vez de fechar."""

    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn
        self._devolvida = False

    def cursor(self):
        return self._conn.cursor()

    def close(self):
        if self._devolvida:
            return
        self._devolvida = True
        self._pool.devolver(self._conn)


class _Pool:
    def __init__(self, tamanho):
        self._livres = queue.LifoQueue(maxsize=tamanho)
        self._vagas = threading.BoundedSemaphore(tamanho)

    def pegar(self):
        if not self._vagas.acquire(timeout=15):
            raise HTTPException(503, "Servico ocupado no momento. Tente de novo.")
        try:
            try:
                conn = self._livres.get_nowait()
            except queue.Empty:
                conn = _connect()
            try:
                conn.ping(reconnect=True)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = _connect()
            return _PooledConn(self, conn)
        except Exception:
            self._vagas.release()
            raise

    def devolver(self, conn):
        try:
            self._livres.put_nowait(conn)
        except queue.Full:
            try:
                conn.close()
            except Exception:
                pass
        finally:
            self._vagas.release()


_POOL = _Pool(DB_POOL)


def db():
    return _POOL.pegar()


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


def token(cliente_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": cliente_id, "exp": exp},
                      JWT_SECRET, algorithm="HS256")


def atual(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Nao autenticado")
    try:
        data = jwt.decode(auth.split(" ", 1)[1], JWT_SECRET, algorithms=["HS256"])
        return {"id": data["sub"]}
    except jwt.PyJWTError:
        raise HTTPException(401, "Sessao invalida ou expirada")


# ---------------- rate limit (em memoria, por processo) ----------------
class RateLimit:
    def __init__(self, limite, janela_segundos):
        self.limite = limite
        self.janela = janela_segundos
        self._dados = {}
        self._lock = threading.Lock()

    def permite(self, chave):
        """Devolve (permitido, segundos_para_tentar_de_novo)."""
        agora = time.time()
        with self._lock:
            if len(self._dados) > 5000:
                self._limpar(agora)
            fila = self._dados.setdefault(chave, deque())
            while fila and fila[0] <= agora - self.janela:
                fila.popleft()
            if len(fila) >= self.limite:
                return False, max(1, int(self.janela - (agora - fila[0])) + 1)
            fila.append(agora)
            return True, 0

    def _limpar(self, agora):
        for k in [k for k, f in self._dados.items()
                  if not f or f[-1] <= agora - self.janela]:
            self._dados.pop(k, None)


LIMITE_CADASTRO_IP = RateLimit(5, 3600)      # 5 cadastros por hora por IP
LIMITE_LOGIN_IP = RateLimit(15, 600)         # 15 tentativas por 10 min por IP
LIMITE_LOGIN_CPF = RateLimit(6, 600)         # 6 tentativas por 10 min por CPF
LIMITE_EXPORT = RateLimit(10, 3600)          # exportar/excluir: 10 por hora
LIMITE_NEWSLETTER = RateLimit(5, 3600)       # 5 inscricoes por hora por IP
LIMITE_OFERTAS = RateLimit(120, 60)          # 120 leituras de ofertas por minuto por IP
LIMITE_OFERTA_IMG = RateLimit(300, 600)      # 300 fotos por 10 min por IP


def ip_do_cliente(request: Request) -> str:
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


def exigir_limite(limite, chave):
    permitido, esperar = limite.permite(chave)
    if not permitido:
        raise HTTPException(429, "Muitas tentativas. Tente novamente em alguns minutos.",
                            headers={"Retry-After": str(esperar)})


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


class Exclusao(BaseModel):
    senha: str
    confirmacao: str


class Inscricao(BaseModel):
    email: str
    origem: str | None = None


class Descadastro(BaseModel):
    token: str


app = FastAPI(
    title="Clube Diana API",
    version="1.5.0",
    docs_url="/docs" if DOCS_ATIVO else None,
    redoc_url="/redoc" if DOCS_ATIVO else None,
    openapi_url="/openapi.json" if DOCS_ATIVO else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
    max_age=600,
)


@app.middleware("http")
async def cabecalhos_de_seguranca(request: Request, call_next):
    resposta = await call_next(request)
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "no-referrer"
    resposta.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    resposta.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resposta.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    # Ofertas sao conteudo publico e muda pouco: pode ficar 60 s no cache do
    # navegador (o endpoint define o max-age). O resto continua no-store.
    caminho = request.url.path
    if caminho != "/api/ofertas" and not caminho.startswith("/api/ofertas/imagem/"):
        resposta.headers["Cache-Control"] = "no-store"
    try:
        del resposta.headers["server"]
    except KeyError:
        pass
    return resposta


@app.exception_handler(Exception)
async def erro_interno(request: Request, exc: Exception):
    # Nunca devolve stack trace nem detalhe interno para o cliente.
    print(f"[clube][erro] {request.method} {request.url.path}: {type(exc).__name__}",
          file=sys.stderr)
    return JSONResponse(status_code=500, content={"detail": "Erro interno"})


@app.get("/api/health")
def health():
    try:
        conn = db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return JSONResponse(status_code=503,
                            content={"ok": False, "service": "clube-diana-api",
                                     "db": "down"})
    return {"ok": True, "service": "clube-diana-api", "db": "up"}


# ---------------- ofertas do site ----------------
# Fonte das ofertas que aparecem em https://clube-diana.onrender.com.
# Quem EDITA e o painel da loja (backend/painel, roda no PC da loja e grava
# direto no banco). A API publica so a leitura, sem senha: e conteudo de
# vitrine. Assim a loja troca oferta sem programador e sem novo deploy.

MIME_IMAGEM = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/svg+xml"}


def _imagem_da_oferta(linha) -> str:
    """Foto da oferta: a enviada pelo painel (fica no banco) ou um arquivo do site."""
    if linha.get("tem_foto"):
        return "/api/ofertas/imagem/" + str(linha["id"])
    return (linha.get("imagem_url") or "").strip()


def _oferta_json(linha) -> dict:
    """Formato que o site consome (o mesmo que o antigo js/ofertas.js)."""
    desconto = linha.get("desconto")
    return {
        "id": str(linha["id"]),
        "nome": linha["produto"],
        "descricao": linha.get("descricao") or "",
        "categoria": linha.get("categoria") or "",
        "precoDe": float(linha.get("preco_de") or 0),
        "precoPor": float(linha.get("preco_por") or 0),
        "unidade": linha.get("unidade") or "kg",
        "desconto": int(desconto) if desconto is not None else 0,
        "imagem": _imagem_da_oferta(linha),
        "destaque": bool(linha.get("destaque")),
    }


@app.get("/api/ofertas")
def ofertas(request: Request):
    exigir_limite(LIMITE_OFERTAS, f"ofertas:{ip_do_cliente(request)}")
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id,produto,descricao,categoria,preco_de,preco_por,unidade,"
                "desconto,imagem_url,(imagem_bytes IS NOT NULL) AS tem_foto,destaque "
                "FROM ofertas WHERE ativo=1 ORDER BY ordem, criado_em")
            linhas = cur.fetchall()
            cur.execute("SELECT MAX(atualizado_em) AS m FROM ofertas WHERE ativo=1")
            linha = cur.fetchone() or {}
            atualizado = linha.get("m")
    finally:
        conn.close()
    corpo = {
        "ofertas": [_oferta_json(l) for l in linhas],
        "atualizado_em": atualizado.strftime("%Y-%m-%d %H:%M:%S") if atualizado else None,
    }
    return JSONResponse(content=corpo, headers={"Cache-Control": "public, max-age=60"})


@app.get("/api/ofertas/imagem/{oferta_id}")
def oferta_imagem(oferta_id: str, request: Request):
    exigir_limite(LIMITE_OFERTA_IMG, f"oferta-img:{ip_do_cliente(request)}")
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT imagem_bytes, imagem_mime FROM ofertas WHERE id=%s",
                        (oferta_id,))
            linha = cur.fetchone()
    finally:
        conn.close()
    if not linha or not linha.get("imagem_bytes"):
        raise HTTPException(404, "Esta oferta nao tem foto")
    dados = bytes(linha["imagem_bytes"])
    mime = linha.get("imagem_mime") or "image/jpeg"
    if mime not in MIME_IMAGEM:
        mime = "image/jpeg"
    etag = '"%s"' % hashlib.sha1(dados).hexdigest()[:24]
    cache = {"Cache-Control": "public, max-age=86400", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache)
    return Response(content=dados, media_type=mime, headers=cache)


@app.post("/api/auth/register")
def register(body: Cadastro, request: Request):
    exigir_limite(LIMITE_CADASTRO_IP, f"cadastro:{ip_do_cliente(request)}")

    cpf = so_digitos(body.cpf)
    if not cpf_valido(cpf):
        raise HTTPException(400, "CPF invalido")

    if not body.aceite:
        raise HTTPException(400, "E necessario aceitar os termos de uso e a politica de privacidade")

    nome = " ".join((body.nome or "").split())
    if len(nome) < 2:
        raise HTTPException(400, "Informe seu nome completo")

    telefone = so_digitos(body.telefone or "")
    if telefone and len(telefone) not in (10, 11):
        raise HTTPException(400, "Telefone invalido")

    nasc = None
    if body.dataNascimento:
        try:
            nasc = datetime.strptime(body.dataNascimento[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "Data de nascimento invalida")
        if nasc > date.today():
            raise HTTPException(400, "Data de nascimento invalida")
        if nasc < date(1900, 1, 1):
            raise HTTPException(400, "Data de nascimento invalida")

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM clientes WHERE cpf=%s", (cpf,))
            if cur.fetchone():
                raise HTTPException(409, "Este CPF ja tem cadastro. Faca login.")
            cid = secrets.token_hex(18)[:36]
            try:
                cur.execute(
                    "INSERT INTO clientes (id,nome,cpf,telefone,data_nascimento,senha_hash,"
                    "aceite_lgpd,aceite_em,aceite_versao,sync_status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),%s,'pendente')",
                    (cid, nome, cpf, telefone or None,
                     nasc, hash_senha(body.senha), 1, VERSAO_POLITICA))
            except pymysql.err.IntegrityError:
                # dois cliques ao mesmo tempo: o UNIQUE(cpf) barra o segundo
                raise HTTPException(409, "Este CPF ja tem cadastro. Faca login.")
    finally:
        conn.close()
    return {"ok": True, "token": token(cid),
            "cliente": {"nome": nome, "cpf": cpf}}


@app.post("/api/auth/login")
def login(body: Login, request: Request):
    exigir_limite(LIMITE_LOGIN_IP, f"login-ip:{ip_do_cliente(request)}")
    cpf = so_digitos(body.cpf)
    exigir_limite(LIMITE_LOGIN_CPF, f"login-cpf:{cpf}")

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id,nome,cpf,senha_hash,excluido_em FROM clientes WHERE cpf=%s",
                        (cpf,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not verifica_senha(body.senha, row["senha_hash"]):
        raise HTTPException(401, "CPF ou senha incorretos")
    if row.get("excluido_em"):
        raise HTTPException(403, "Esta conta foi excluida. Fale com a loja para voltar ao clube.")
    return {"ok": True, "token": token(row["id"]),
            "cliente": {"nome": row["nome"], "cpf": row["cpf"]}}


def _periodo(de, ate):
    """Normaliza o filtro de datas (YYYY-MM-DD). Devolve (date|None, date|None)."""
    def conv(v):
        if not v:
            return None
        try:
            return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "Data invalida")

    d, a = conv(de), conv(ate)
    if d and a and d > a:
        raise HTTPException(400, "Periodo invalido")
    return d, a


def _filtro(where, params, d, a):
    if d:
        where += " AND data_venda >= %s"
        params.append(d)
    if a:
        where += " AND data_venda < DATE_ADD(%s, INTERVAL 1 DAY)"
        params.append(a)
    return where, params


@app.get("/api/me/compras")
def compras(user=Depends(atual), limit: int = 20, offset: int = 0,
            de: str | None = None, ate: str | None = None):
    """Historico completo do cliente, com filtro de periodo e paginacao."""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    d, a = _periodo(de, ate)
    where, params = _filtro("cliente_id=%s", [user["id"]], d, a)

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM compras WHERE " + where, tuple(params))
            total = int(cur.fetchone()["n"])
            cur.execute(
                "SELECT data_venda, total_liquido, total_desconto, cupom "
                "FROM compras WHERE " + where +
                " ORDER BY data_venda DESC LIMIT %s OFFSET %s",
                tuple(params) + (limit, offset))
            rows = cur.fetchall()
    finally:
        conn.close()

    for r in rows:
        r["data_venda"] = r["data_venda"].strftime("%d/%m/%Y %H:%M")
        r["total_liquido"] = float(r["total_liquido"] or 0)
        r["total_desconto"] = float(r["total_desconto"] or 0)

    return {"ok": True, "total": total, "offset": offset,
            "tem_mais": offset + len(rows) < total,
            "periodo": {"de": d.isoformat() if d else None,
                        "ate": a.isoformat() if a else None},
            "compras": rows}


@app.get("/api/me/resumo")
def resumo(user=Depends(atual), de: str | None = None, ate: str | None = None):
    """Totais do periodo (padrao: tudo) + totais de vida do cliente."""
    d, a = _periodo(de, ate)
    where, params = _filtro("cliente_id=%s", [user["id"]], d, a)

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS qtd, COALESCE(SUM(total_liquido),0) AS gasto, "
                "COALESCE(SUM(total_desconto),0) AS economia, MAX(data_venda) AS ultima "
                "FROM compras WHERE " + where, tuple(params))
            r = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*) AS qtd, COALESCE(SUM(total_liquido),0) AS gasto, "
                "COALESCE(SUM(total_desconto),0) AS economia, "
                "MIN(data_venda) AS primeira, MAX(data_venda) AS ultima "
                "FROM compras WHERE cliente_id=%s", (user["id"],))
            g = cur.fetchone()
            cur.execute("SELECT nome,cpf,criado_em FROM clientes WHERE id=%s", (user["id"],))
            c = cur.fetchone() or {}
    finally:
        conn.close()

    def br(dt):
        return dt.strftime("%d/%m/%Y") if dt else None

    return {"ok": True,
            "cliente": {"nome": c.get("nome"), "cpf": c.get("cpf"),
                        "desde": br(c.get("criado_em"))},
            "periodo": {"de": d.isoformat() if d else None,
                        "ate": a.isoformat() if a else None},
            "compras": int(r["qtd"]),
            "total_gasto": float(r["gasto"] or 0),
            "total_economizado": float(r["economia"] or 0),
            "ultima_compra": br(r["ultima"]),
            "geral": {"compras": int(g["qtd"]),
                      "total_gasto": float(g["gasto"] or 0),
                      "total_economizado": float(g["economia"] or 0),
                      "primeira_compra": br(g["primeira"]),
                      "ultima_compra": br(g["ultima"])}}


@app.get("/api/me/dados")
def meus_dados(user=Depends(atual)):
    """LGPD art. 18, II: copia completa dos dados que o clube guarda."""
    exigir_limite(LIMITE_EXPORT, f"export:{user['id']}")

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nome,cpf,telefone,email,data_nascimento,criado_em,atualizado_em,"
                "aceite_em,aceite_versao,sync_status,last_synced_at "
                "FROM clientes WHERE id=%s", (user["id"],))
            c = cur.fetchone()
            if not c:
                raise HTTPException(404, "Conta nao encontrada")
            cur.execute(
                "SELECT data_venda,total_produtos,total_desconto,total_liquido,cupom,itens_json "
                "FROM compras WHERE cliente_id=%s ORDER BY data_venda DESC", (user["id"],))
            rows = cur.fetchall()
    finally:
        conn.close()

    def data(v):
        return v.strftime("%Y-%m-%d") if v else None

    def data_hora(v):
        return v.strftime("%Y-%m-%d %H:%M:%S") if v else None

    compras = []
    for r in rows:
        itens = r.get("itens_json")
        if isinstance(itens, str):
            try:
                itens = json.loads(itens)
            except ValueError:
                itens = []
        compras.append({
            "data": data_hora(r["data_venda"]),
            "total_produtos": float(r["total_produtos"] or 0),
            "total_desconto": float(r["total_desconto"] or 0),
            "total_liquido": float(r["total_liquido"] or 0),
            "cupom": r["cupom"],
            "itens": itens or [],
        })

    return {
        "ok": True,
        "gerado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "titular": {
            "nome": c["nome"],
            "cpf": c["cpf"],
            "telefone": c["telefone"],
            "email": c["email"],
            "data_nascimento": data(c["data_nascimento"]),
            "conta_criada_em": data_hora(c["criado_em"]),
            "ultima_atualizacao": data_hora(c["atualizado_em"]),
            "consentimento_em": data_hora(c.get("aceite_em")),
            "consentimento_versao": c.get("aceite_versao"),
            "situacao_no_clube": c.get("sync_status"),
            "sincronizado_com_a_loja_em": data_hora(c.get("last_synced_at")),
        },
        "compras": compras,
        "observacoes": (
            "Este arquivo contem os dados do Clube Diana. As compras ficam "
            "vinculadas ao seu CPF apenas quando voce informa o CPF no caixa."
        ),
    }


@app.post("/api/me/excluir")
def excluir_conta(body: Exclusao, user=Depends(atual)):
    """LGPD art. 18, VI: apaga os dados pessoais e desativa a conta.

    O historico de compras permanece (obrigacao fiscal) mas sem nome, CPF,
    telefone ou e-mail: fica ligado apenas a um identificador anonimo.
    """
    if (body.confirmacao or "").strip().upper() != "EXCLUIR":
        raise HTTPException(400, "Digite EXCLUIR para confirmar")
    exigir_limite(LIMITE_EXPORT, f"exclusao:{user['id']}")

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT cpf,senha_hash,excluido_em FROM clientes WHERE id=%s",
                        (user["id"],))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Conta nao encontrada")
            if row.get("excluido_em"):
                raise HTTPException(409, "Esta conta ja foi excluida")
            if not verifica_senha(body.senha, row["senha_hash"]):
                raise HTTPException(401, "Senha incorreta")

            # identificador anonimo, unico e irreversivel (nao e mais um CPF)
            anonimo = "EX" + hashlib.sha256(
                (row["cpf"] + JWT_SECRET).encode()).hexdigest()[:9]
            cur.execute(
                "UPDATE clientes SET nome=%s, cpf=%s, telefone=NULL, email=NULL, "
                "data_nascimento=NULL, senha_hash=%s, excluido_em=NOW(), "
                "sync_status='ignorado', sync_erro=NULL WHERE id=%s",
                ("Titular excluido", anonimo,
                 hash_senha(secrets.token_hex(16)), user["id"]))
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/newsletter")
def newsletter(body: Inscricao, request: Request):
    exigir_limite(LIMITE_NEWSLETTER, f"news:{ip_do_cliente(request)}")

    email = (body.email or "").strip().lower()
    if len(email) > 190 or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", email):
        raise HTTPException(400, "E-mail invalido")

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO newsletter (id,email,origem,token) VALUES (%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE origem=VALUES(origem), "
                "token=COALESCE(newsletter.token, VALUES(token))",
                (secrets.token_hex(18)[:36], email, (body.origem or "site")[:40],
                 secrets.token_hex(16)))
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/newsletter/sair")
def sair_newsletter(body: Descadastro, request: Request):
    exigir_limite(LIMITE_NEWSLETTER, f"sair:{ip_do_cliente(request)}")
    tok = (body.token or "").strip().lower()
    if not re.match(r"^[a-f0-9]{32}$", tok):
        raise HTTPException(400, "Link invalido")

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM newsletter WHERE token=%s", (tok,))
            if not cur.fetchone():
                raise HTTPException(404, "Link invalido ou expirado")
            cur.execute("UPDATE newsletter SET descadastrado_em=NOW() "
                        "WHERE token=%s AND descadastrado_em IS NULL", (tok,))
    finally:
        conn.close()
    return {"ok": True}

# ---------------- recuperacao de senha por WhatsApp (Fase 3b) ----------------
#
# O codigo de 6 digitos NAO e gerado aqui: ele nasce no PC da loja
# (sync/envia_codigos.py), que guarda so o hash e envia pelo WhatsApp.
# Aqui apenas criamos o pedido e conferimos o codigo digitado.

class Esqueci(BaseModel):
    cpf: str


class Redefinir(BaseModel):
    cpf: str
    codigo: str
    senha: str = Field(min_length=6, max_length=100)


LIMITE_ESQUECI_CPF = RateLimit(3, 3600)      # 3 pedidos por hora por CPF
LIMITE_ESQUECI_IP = RateLimit(10, 3600)      # 10 por hora por IP
LIMITE_REDEFINIR = RateLimit(10, 3600)       # 10 tentativas por hora por IP

MINUTOS_PARA_ENVIAR = 15                     # prazo ate o agente enviar
TENTATIVAS_CODIGO = 5                        # erros antes de invalidar


@app.post("/api/auth/esqueci")
def esqueci_senha(body: Esqueci, request: Request):
    """Cria o pedido de codigo. Resposta e sempre igual (nao revela cadastro)."""
    exigir_limite(LIMITE_ESQUECI_IP, f"esqueci-ip:{ip_do_cliente(request)}")
    cpf = so_digitos(body.cpf)
    exigir_limite(LIMITE_ESQUECI_CPF, f"esqueci-cpf:{cpf}")

    resposta = {"ok": True,
                "mensagem": "Se este CPF tiver cadastro, o codigo chega no WhatsApp "
                            "em ate 1 minuto."}
    if not cpf_valido(cpf):
        return resposta

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, telefone, excluido_em FROM clientes WHERE cpf=%s", (cpf,))
            row = cur.fetchone()
            if not row or row.get("excluido_em"):
                return resposta
            telefone = so_digitos(row.get("telefone") or "")
            if len(telefone) not in (10, 11):
                return resposta

            # um pedido por vez
            cur.execute("UPDATE recuperacao SET usado_em=NOW() "
                        "WHERE cliente_id=%s AND usado_em IS NULL", (row["id"],))
            cur.execute(
                "INSERT INTO recuperacao (id, cliente_id, codigo_hash, telefone, expira_em) "
                "VALUES (%s,%s,NULL,%s, DATE_ADD(NOW(), INTERVAL %s MINUTE))",
                (secrets.token_hex(18)[:36], row["id"], telefone, MINUTOS_PARA_ENVIAR))
    finally:
        conn.close()
    return resposta


@app.post("/api/auth/redefinir")
def redefinir_senha(body: Redefinir, request: Request):
    exigir_limite(LIMITE_REDEFINIR, f"redefinir:{ip_do_cliente(request)}")
    cpf = so_digitos(body.cpf)
    codigo = "".join(c for c in (body.codigo or "") if c.isdigit())
    if not cpf_valido(cpf) or len(codigo) != 6:
        raise HTTPException(400, "Codigo invalido ou expirado")

    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, excluido_em FROM clientes WHERE cpf=%s", (cpf,))
            cli = cur.fetchone()
            if not cli or cli.get("excluido_em"):
                raise HTTPException(400, "Codigo invalido ou expirado")

            cur.execute(
                "SELECT id, codigo_hash, tentativas FROM recuperacao "
                "WHERE cliente_id=%s AND usado_em IS NULL AND codigo_hash IS NOT NULL "
                "AND expira_em > NOW() ORDER BY criado_em DESC LIMIT 1", (cli["id"],))
            reg = cur.fetchone()
            if not reg or int(reg["tentativas"]) >= TENTATIVAS_CODIGO:
                raise HTTPException(400, "Codigo invalido ou expirado")

            if not verifica_senha(codigo, reg["codigo_hash"]):
                cur.execute("UPDATE recuperacao SET tentativas=tentativas+1 WHERE id=%s",
                            (reg["id"],))
                raise HTTPException(400, "Codigo invalido ou expirado")

            cur.execute("UPDATE clientes SET senha_hash=%s WHERE id=%s",
                        (hash_senha(body.senha), cli["id"]))
            cur.execute("UPDATE recuperacao SET usado_em=NOW() WHERE id=%s", (reg["id"],))
            cur.execute("UPDATE recuperacao SET usado_em=NOW() "
                        "WHERE cliente_id=%s AND usado_em IS NULL", (cli["id"],))
    finally:
        conn.close()
    return {"ok": True, "mensagem": "Senha alterada. Entre com a senha nova."}
