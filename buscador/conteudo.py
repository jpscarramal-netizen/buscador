"""Busca dentro dos arquivos: índice de texto completo (SQLite FTS5) guardado no D:.

O indexador roda num processo à parte, com prioridade baixa, e recebe da janela a lista de
arquivos candidatos (tirada do índice de nomes). Cada página do PDF vira uma linha, para a
busca dizer em que página está o trecho. Cópias (mesmo nome e mesmo tamanho) entram uma
vez só. O índice aguenta ser interrompido: na próxima vez continua de onde parou.

Uso: python -m buscador.conteudo LISTA.txt
"""
import os
import re
import sqlite3
import sys
import time

from .config import PASTA_GRANDE
from .extrair import longo
from .indice import chave
from .sinonimos import expandir

BANCO = os.path.join(PASTA_GRANDE, "conteudo.db")
LISTA = os.path.join(PASTA_GRANDE, "candidatos.txt")
MAX_PAGINAS = 3000
MAX_BYTES = {"pdf": 600 * 1024 ** 2, "docx": 80 * 1024 ** 2, "pptx": 300 * 1024 ** 2,
             "xlsx": 80 * 1024 ** 2, "xlsm": 80 * 1024 ** 2}
MAX_TEXTO = 5 * 1024 ** 2


def conectar(so_leitura=False):
    os.makedirs(PASTA_GRANDE, exist_ok=True)
    con = sqlite3.connect(BANCO, timeout=15)
    con.execute("pragma busy_timeout=15000")
    if not so_leitura:
        con.execute("pragma journal_mode=wal")
        con.execute("pragma synchronous=normal")
        con.executescript("""
            create table if not exists docs(
                id integer primary key, caminho text unique, chave text, tam integer, mtime real,
                paginas integer, estado text);
            create index if not exists docs_chave on docs(chave);
            create virtual table if not exists pags using fts5(
                texto, doc unindexed, pag unindexed, tokenize="unicode61 remove_diacritics 2");
        """)
    return con


def existe():
    return os.path.exists(BANCO)


def _chave_dup(caminho, tam):
    return f"{chave(os.path.basename(caminho))}|{tam}"


# ---- extração ---------------------------------------------------------------
def _paginas(caminho):
    """[(número da página, texto)] do arquivo."""
    from . import extrair, ocr
    e = extrair.ext(caminho)
    if e == "pdf":
        import pymupdf
        feito = ocr.carregar(caminho) or {}
        saida = []
        with pymupdf.open(extrair.longo(caminho)) as doc:
            if doc.needs_pass:
                return []
            for k in range(min(doc.page_count, MAX_PAGINAS)):
                try:
                    t = extrair.paragrafos(doc[k]).strip()
                except Exception:
                    t = ""
                if not t and k in feito:
                    t = ocr.texto_das_palavras(feito[k])
                saida.append((k + 1, t))
        return saida
    if e in extrair.EXT_OFFICE:
        return [(k, t) for k, (_, t) in enumerate(extrair.office(caminho), 1)]
    if os.path.getsize(extrair.longo(caminho)) > MAX_TEXTO:
        return []
    return [(1, extrair.texto_puro(caminho))]


def gravar_paginas(con, caminho, paginas):
    """Troca o texto de um arquivo no índice (usado também depois de um OCR)."""
    st = os.stat(longo(caminho))
    con.execute("delete from pags where doc in (select id from docs where caminho=?)", (caminho,))
    con.execute("delete from docs where caminho=?", (caminho,))
    com_texto = [(t, k) for k, t in paginas if t.strip()]
    cur = con.execute("insert into docs(caminho, chave, tam, mtime, paginas, estado) values (?,?,?,?,?,?)",
                      (caminho, _chave_dup(caminho, st.st_size), st.st_size, st.st_mtime, len(paginas),
                       "ok" if com_texto else "vazio"))
    con.executemany("insert into pags(texto, doc, pag) values (?,?,?)", ((t, cur.lastrowid, k) for t, k in com_texto))


# ---- indexador (processo separado) ------------------------------------------
def indexar(lista):
    con = conectar()
    with open(lista, encoding="utf-8") as f:
        candidatos = [l.rstrip("\n") for l in f if l.strip()]
    queridos = set(candidatos)

    # tira do índice o que sumiu ou saiu da lista; as cópias dele voltam para a fila
    sumidos = [(i, c, k) for i, c, k in con.execute("select id, caminho, chave from docs") if c not in queridos]
    for i, c, k in sumidos:
        con.execute("delete from pags where doc=?", (i,))
        con.execute("delete from docs where id=?", (i,))
        con.execute("delete from docs where estado='dup' and chave=?", (k,))
    con.commit()

    feitos = {c: (t, m) for c, t, m in con.execute("select caminho, tam, mtime from docs")}
    fila = []
    for c in candidatos:
        try:
            st = os.stat(longo(c))
        except OSError:
            continue
        if feitos.get(c) == (st.st_size, st.st_mtime):
            continue
        if st.st_size > MAX_BYTES.get(c.rsplit(".", 1)[-1].lower(), MAX_TEXTO):
            continue
        fila.append((st.st_mtime, c, st.st_size))
    fila.sort(reverse=True)           # os mais recentes primeiro
    total = len(fila)
    print(f"T\t{total}\t{len(feitos)}", flush=True)

    ultimo = time.time()
    for n, (_, c, tam) in enumerate(fila, 1):
        k = _chave_dup(c, tam)
        orig = con.execute("select caminho from docs where chave=? and estado!='dup' and caminho!=? limit 1",
                           (k, c)).fetchone()
        try:
            if orig and os.path.exists(longo(orig[0])):
                con.execute("delete from pags where doc in (select id from docs where caminho=?)", (c,))
                con.execute("insert or replace into docs(caminho, chave, tam, mtime, paginas, estado) "
                            "values (?,?,?,?,0,'dup')", (c, k, tam, os.path.getmtime(longo(c))))
            else:
                gravar_paginas(con, c, _paginas(c))
        except Exception:
            try:
                con.execute("insert or replace into docs(caminho, chave, tam, mtime, paginas, estado) "
                            "values (?,?,?,?,0,'erro')", (c, k, tam, os.path.getmtime(longo(c))))
            except Exception:          # arquivo sumiu ou banco ocupado: segue para o próximo
                pass
        if time.time() - ultimo > 2 or n == total:
            con.commit()
            ultimo = time.time()
            print(f"P\t{n}\t{total}", flush=True)
    if total > 500:
        con.execute("insert into pags(pags) values('optimize')")
    con.commit()
    con.close()
    print("F", flush=True)


# ---- busca ------------------------------------------------------------------
def _fts(termos):
    """Consulta FTS5: cada palavra (ou frase entre aspas) com seus sinônimos; todas obrigatórias."""
    partes = []
    for t in termos:
        t = t.replace('"', " ").strip()
        if not t:
            continue
        palavras = [t] if " " in t else t.split()
        for p in palavras:
            alts = []
            for a in expandir(chave(p)):
                a = " ".join(re.findall(r"\w+", a))
                if not a:
                    continue
                alts.append(f'"{a}"*' if " " not in a and len(a) >= 4 else f'"{a}"')
            if alts:
                partes.append("(" + " OR ".join(dict.fromkeys(alts)) + ")")
    return " AND ".join(partes)


def procurar(termos, limite=4000):
    """[(caminho, página, trecho, páginas com achado)] na ordem de relevância."""
    q = _fts(termos)
    if not q or not existe():
        return []
    con = conectar(so_leitura=True)
    try:
        linhas = con.execute(
            "select doc, pag, snippet(pags, 0, '«', '»', '…', 16) from pags where pags match ? "
            "order by bm25(pags) limit ?", (q, limite)).fetchall()
        por_doc = {}
        for doc, pag, trecho in linhas:
            if doc in por_doc:
                por_doc[doc][3] += 1
            else:
                por_doc[doc] = [doc, pag, " ".join(trecho.split()), 1]
        if not por_doc:
            return []
        ids = list(por_doc)
        caminhos = {}
        for k in range(0, len(ids), 900):
            lote = ids[k:k + 900]
            caminhos.update(con.execute(f"select id, caminho from docs where id in ({','.join('?' * len(lote))})", lote))
        return [(caminhos[d], p, t, n) for d, p, t, n in por_doc.values() if d in caminhos]
    finally:
        con.close()


def resumo():
    """(arquivos com texto, cópias puladas, sem texto, tamanho do banco em MB)."""
    if not existe():
        return 0, 0, 0, 0
    con = conectar(so_leitura=True)
    try:
        cont = dict(con.execute("select estado, count(*) from docs group by estado").fetchall())
    finally:
        con.close()
    mb = sum(os.path.getsize(BANCO + s) for s in ("", "-wal") if os.path.exists(BANCO + s)) / 1024 ** 2
    return cont.get("ok", 0), cont.get("dup", 0), cont.get("vazio", 0), mb


if __name__ == "__main__":
    indexar(sys.argv[1] if len(sys.argv) > 1 else LISTA)
