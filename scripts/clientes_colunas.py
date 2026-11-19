"""Mostra a estrutura da tabela clientes e de onde cada cadastro veio."""
import io
import os
from urllib.parse import urlparse, unquote

import pymysql
import pymysql.cursors

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
d = {}
for l in io.open(os.path.join(RAIZ, "backend", ".env"), encoding="utf-8"):
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            d[k.strip()] = v

p = urlparse(d["CLUBE_DATABASE_URL"])
c = pymysql.connect(host=p.hostname, port=p.port or 4000,
                    user=unquote(p.username or ""), password=unquote(p.password or ""),
                    database=(p.path or "/").lstrip("/"), charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    ssl={"ssl": {}} if "tidb" in (p.hostname or "") else None, autocommit=True)
cur = c.cursor()
cur.execute("SHOW COLUMNS FROM clientes")
for r in cur.fetchall():
    print(f'{r["Field"]:22} {r["Type"]}')
c.close()
