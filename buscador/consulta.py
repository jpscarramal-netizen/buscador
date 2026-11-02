"""Sintaxe de busca no estilo do Everything.

- palavras separadas por espaço: todas precisam aparecer (E)
- a|b: uma ou outra (OU)          - !palavra: exclui
- * e ?: curinga no nome inteiro  - "frase com espaço"
- ext:pdf;docx   pasta: / folder:   arquivo: / file:   path:termo (procura no caminho)
- termo com \\ procura no caminho completo
- dm:hoje | ontem | semana | mes | ano | 7d | 01/10/2026 | >01/10/2026 | 01/10/2026..15/10/2026
- tamanho:>10mb | size:<500kb | tamanho:1mb..20mb | tamanho:vazio
- conteudo:termo (procura dentro dos arquivos; tratado na janela, com o índice do conteúdo)
Sem diferença de acento; siglas do arquivo de sinônimos se expandem (iam -> infarto...).
"""
import re
from array import array
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from heapq import merge

from .indice import chave
from .sinonimos import expandir

PREF_CONTEUDO = ("conteudo:", "conteúdo:", "content:", "texto:")
PREF_DATA = ("dm:", "datemodified:", "data:")
PREF_TAM = ("tamanho:", "size:", "tam:")
UNIDADES = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}


def _tokens(q):
    return re.findall(r'!?(?:\w+:)?"[^"]*"|\S+', q)


def separar_conteudo(consulta):
    """(termos de conteúdo, resto da consulta). Ex.: 'cx conteudo:"lesão hepática"' -> (['lesão hepática'], 'cx')."""
    cont, resto = [], []
    for t in _tokens(consulta):
        low = t.lower()
        pref = next((p for p in PREF_CONTEUDO if low.startswith(p)), None)
        if pref:
            v = t[len(pref):].replace('"', "").strip()
            if v:
                cont.append(v)
        else:
            resto.append(t)
    return cont, " ".join(resto)


def _dia(txt):
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(txt, fmt)
            return d, d + timedelta(days=1)
        except ValueError:
            pass
    try:
        d = datetime.strptime(txt, "%m/%Y")
        return d, (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    except ValueError:
        pass
    if re.fullmatch(r"\d{4}", txt):
        d = datetime(int(txt), 1, 1)
        return d, d.replace(year=d.year + 1)
    return None


def _faixa_data(v):
    """Intervalo [lo, hi) em timestamp para dm:..."""
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    um = timedelta(days=1)
    nomes = {"hoje": (hoje, None), "today": (hoje, None),
             "ontem": (hoje - um, hoje), "yesterday": (hoje - um, hoje),
             "semana": (hoje - 7 * um, None), "week": (hoje - 7 * um, None),
             "mes": (hoje - 30 * um, None), "mês": (hoje - 30 * um, None), "month": (hoje - 30 * um, None),
             "ano": (hoje - 365 * um, None), "year": (hoje - 365 * um, None)}
    if v in nomes:
        lo, hi = nomes[v]
    elif re.fullmatch(r"\d+d", v):
        lo, hi = hoje - int(v[:-1]) * um, None
    elif ".." in v:
        a, b = (_dia(x) for x in v.split("..", 1))
        if not a or not b:
            return None
        lo, hi = a[0], b[1]
    else:
        m = re.fullmatch(r"(>=|<=|>|<)?(.+)", v)
        d = _dia(m.group(2)) if m else None
        if not d:
            return None
        lo, hi = {None: d, ">": (d[1], None), ">=": (d[0], None), "<": (None, d[0]), "<=": (None, d[1])}[m.group(1)]
    return (lo.timestamp() if lo else float("-inf"), hi.timestamp() if hi else float("inf"))


def _bytes(txt):
    m = re.fullmatch(r"([\d.,]+)\s*(b|kb|mb|gb)?", txt)
    if not m:
        return None
    u = UNIDADES[m.group(2) or "b"]
    return int(float(m.group(1).replace(",", ".")) * u), u


def _faixa_tam(v):
    if v in ("vazio", "empty", "0"):
        return 0, 1
    if ".." in v:
        a, b = (_bytes(x) for x in v.split("..", 1))
        return (a[0], b[0] + b[1]) if a and b else None
    m = re.fullmatch(r"(>=|<=|>|<)?(.+)", v)
    b = _bytes(m.group(2)) if m else None
    if not b:
        return None
    n, u = b
    return {None: (n, n + u), ">": (n + 1, float("inf")), ">=": (n, float("inf")),
            "<": (0, n), "<=": (0, n + 1)}[m.group(1)]


def _curinga(t):
    return "".join("[^\n]*" if c == "*" else "[^\n]" if c == "?" else re.escape(c) for c in t)


def _literal_curinga(a):
    """Maior pedaço fixo de um curinga, ancorado em '\\n' quando encosta no começo ou fim do nome."""
    pedacos = re.split(r"[*?]+", a)
    melhor = ""
    for k, p in enumerate(pedacos):
        if not p:
            continue
        lit = ("\n" if k == 0 else "") + p + ("\n" if k == len(pedacos) - 1 else "")
        if len(lit) > len(melhor):
            melhor = lit
    return melhor or None


class Termo:
    """Um termo da busca: sabe dar literais para varrer a string de nomes e testar um item."""

    def __init__(self, bruto):
        self.neg = bruto.startswith("!")
        t = bruto[1:] if self.neg else bruto
        self.tipo = None          # "pasta" | "arquivo"
        self.no_caminho = False
        self.ext = None
        self.faixa = None         # ("data" | "tam", lo, hi)
        low = t.lower()
        for pref, tipo in (("folder:", "pasta"), ("pasta:", "pasta"), ("file:", "arquivo"), ("arquivo:", "arquivo")):
            if low.startswith(pref):
                self.tipo = tipo
                t = t[len(pref):]
                low = t.lower()
        if low.startswith("path:") or low.startswith("caminho:"):
            self.no_caminho = True
            t = t.split(":", 1)[1]
            low = t.lower()
        if low.startswith("ext:"):
            self.ext = [e.strip(".") for e in low[4:].split(";") if e.strip(".")]
            t = ""
        for prefs, tipo, fn in ((PREF_DATA, "data", _faixa_data), (PREF_TAM, "tam", _faixa_tam)):
            pref = next((p for p in prefs if low.startswith(p)), None)
            if pref:
                f = fn(low[len(pref):].replace('"', "").strip())
                self.faixa = (tipo, *f) if f else None
                t = ""
        if low.startswith(PREF_CONTEUDO):
            t = ""
        t = chave(t.replace('"', ""))
        if "\\" in t or "/" in t:
            self.no_caminho = True
            t = t.replace("/", "\\")
        # siglas e sinônimos entram como alternativas (só palavra simples, sem curinga nem caminho)
        self.alternativas = []
        for a in (a for a in t.split("|") if a):
            for x in ([a] if "*" in a or "?" in a or self.no_caminho else expandir(a)):
                if x not in self.alternativas:
                    self.alternativas.append(x)
        partes = ["^" + _curinga(a) + "$" if ("*" in a or "?" in a) else re.escape(a) for a in self.alternativas]
        self.regex = re.compile("|".join(partes)) if partes else None
        self.regex_ext = re.compile(r"\.(?:" + "|".join(map(re.escape, self.ext)) + r")$") if self.ext else None
        self.vazio = self.regex is None and self.regex_ext is None and self.tipo is None and self.faixa is None
        # só substring simples: o literal já garante o resultado, dispensa conferir item a item
        self.exato = (not self.neg and not self.no_caminho and self.tipo is None and self.ext is None
                      and self.faixa is None and "*" not in t and "?" not in t)

    def literais(self):
        """Textos que todo nome encontrado contém (na string '\\n'-separada); None = não dá para guiar."""
        if self.neg:
            return None
        if self.ext and not self.alternativas:
            return ["." + e + "\n" for e in self.ext]
        if self.no_caminho or not self.alternativas:
            return None
        lits = []
        for a in self.alternativas:
            lit = _literal_curinga(a) if ("*" in a or "?" in a) else a
            if not lit:
                return None
            lits.append(lit)
        return lits

    def testa(self, idx, i):
        ok = True
        if self.tipo == "pasta":
            ok = bool(idx.pasta[i])
        elif self.tipo == "arquivo":
            ok = not idx.pasta[i]
        if ok and self.faixa is not None:
            tipo, lo, hi = self.faixa
            v = idx.data[i] if tipo == "data" else idx.tam[i]
            ok = lo <= v < hi and (tipo == "data" or v >= 0)
        if ok and self.regex_ext is not None:
            ok = not idx.pasta[i] and self.regex_ext.search(idx.baixos[i]) is not None
        if ok and self.regex is not None:
            alvo = chave(idx.caminho(i)) if self.no_caminho else idx.baixos[i]
            ok = self.regex.search(alvo) is not None
        return ok != self.neg


def _varrer_literal(idx, lit, cancelado):
    """Posições dos nomes da base que contêm lit; pula para o próximo nome a cada achado."""
    blob, ini = idx.blob, idx.inicios
    find = blob.find
    ancora = lit.startswith("\n")
    ultimo = len(ini) - 1
    out = array("i")
    pos = find(lit)
    k = 0
    while pos >= 0:
        i = bisect_right(ini, pos + 1 if ancora else pos) - 1
        if i >= ultimo:
            break
        out.append(i)
        prox = ini[i + 1]
        pos = find(lit, prox - 1 if ancora else prox)
        k += 1
        if k & 0x3FFF == 0 and cancelado():
            return None
    return out


def buscar(idx, consulta, cancelado=lambda: False, limitar=None):
    """Devolve array('i') de posições no índice, em ordem de nome (None se cancelada).

    limitar: posições já escolhidas (ex.: achados no conteúdo), na ordem em que vieram;
    os termos da consulta só filtram essas.
    """
    termos = [t for t in (Termo(b) for b in _tokens(consulta)) if not t.vazio]
    if limitar is not None:
        with idx.trava:
            return array("i", (i for i in limitar if i < len(idx.apagado) and not idx.apagado[i]
                               and all(t.testa(idx, i) for t in termos)))
    with idx.trava:
        n, nbase = len(idx.nomes), idx.nbase
        apag = idx.apagado
        base_apag = bytes(apag[:nbase])
        apagados_base = []
        k = base_apag.find(1)
        while k >= 0:
            apagados_base.append(k)
            k = base_apag.find(1, k + 1)
        # o termo com o literal mais longo guia a busca na string de nomes
        guias = [(min(map(len, t.literais())), t) for t in termos if t.literais()]
        motor = max(guias, key=lambda g: g[0])[1] if guias else None

        if motor is not None:
            listas = []
            for lit in motor.literais():
                r = _varrer_literal(idx, lit, cancelado)
                if r is None:
                    return None
                listas.append(r)
            base = listas[0] if len(listas) == 1 else array("i", sorted(set().union(*listas)))
            resto = [t for t in termos if t is not motor or not t.exato]
            delta = [i for i in range(nbase, n) if motor.testa(idx, i)]
        else:
            faixas = [t for t in termos if t.faixa is not None and not t.neg]
            if faixas:
                # filtro de data ou tamanho sem palavra: varre só o array numérico
                tipo, lo, hi = faixas[0].faixa
                arr = idx.data if tipo == "data" else idx.tam
                base = array("i", (i for i, v in enumerate(arr[:nbase]) if lo <= v < hi))
            else:
                base = range(nbase)
            resto = termos
            delta = list(range(nbase, n))

        if resto:
            saida = array("i")
            for k, i in enumerate(base):
                if not apag[i] and all(t.testa(idx, i) for t in resto):
                    saida.append(i)
                if k & 0x3FFFF == 0 and cancelado():
                    return None
            delta = [i for i in delta if not apag[i] and all(t.testa(idx, i) for t in resto)]
        else:
            saida = base if isinstance(base, array) else array("i", base)
            if apagados_base:
                # poucos apagados: tira cada um por bisseção em vez de varrer a lista inteira
                saida = array("i", saida)
                for d in apagados_base:
                    p = bisect_left(saida, d)
                    if p < len(saida) and saida[p] == d:
                        del saida[p]
            delta = [i for i in delta if not apag[i]]

        if delta:
            delta.sort(key=idx.baixos.__getitem__)
            saida = array("i", merge(saida, delta, key=idx.baixos.__getitem__))
        return saida
