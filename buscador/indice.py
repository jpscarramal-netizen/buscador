"""Índice de nomes de todos os arquivos e pastas das unidades fixas (como o Everything).

Os nomes ficam ordenados e colados numa string única separada por '\n'; a busca roda
uma expressão regular sobre essa string (em C, rápida) e devolve os índices já em ordem
de nome. Arquivos criados depois da varredura vão para uma lista "delta", varrida à parte.
"""
import ctypes
import os
import pickle
import string
import threading
import time
from array import array
from bisect import bisect_left
from itertools import accumulate

from .config import PASTA_DADOS

CACHE = os.path.join(PASTA_DADOS, "indice.pkl")
VERSAO_CACHE = 2          # 2 = ordem pela chave sem acento
_SEM_ACENTO = str.maketrans("áàâãäåéèêëíìîïóòôõöúùûüçñýÿ", "aaaaaaeeeeiiiiooooouuuucnyy")


def chave(nome):
    """Forma de comparar nomes: minúsculas e sem acento."""
    b = nome.lower()
    return b if b.isascii() else b.translate(_SEM_ACENTO)


def unidades_fixas():
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    return [f"{l}:\\" for i, l in enumerate(string.ascii_uppercase)
            if mask >> i & 1 and k32.GetDriveTypeW(f"{l}:\\") == 3]  # 3 = DRIVE_FIXED


def varrer(raizes, progresso=None):
    """Percorre as unidades e devolve listas cruas (pais apontam para posições cruas)."""
    nomes, pais, pasta, tam, data = [], array("i"), bytearray(), array("q"), array("d")
    aviso = 50000
    for raiz in raizes:
        ir = len(nomes)
        nomes.append(raiz.rstrip("\\"))
        pais.append(-1); pasta.append(1); tam.append(-1); data.append(0.0)
        pilha = [(raiz, ir)]
        while pilha:
            caminho, ip = pilha.pop()
            try:
                it = os.scandir(caminho)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)  # no Windows vem de graça do FindNextFile
                        d = e.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    i = len(nomes)
                    nomes.append(e.name)
                    pais.append(ip); pasta.append(1 if d else 0)
                    tam.append(-1 if d else st.st_size); data.append(st.st_mtime)
                    if d and not e.is_junction() and not e.is_symlink():
                        pilha.append((e.path, i))
            if progresso and len(nomes) >= aviso:
                progresso(raiz, len(nomes))
                aviso = len(nomes) + 50000
    return nomes, pais, pasta, tam, data


class Indice:
    def __init__(self):
        self.trava = threading.RLock()
        self.nomes = []
        self.baixos = []
        self.pais = array("i")
        self.pasta = bytearray()
        self.tam = array("q")
        self.data = array("d")
        self.apagado = bytearray()
        self.blob = "\n"
        self.inicios = array("q")
        self.nbase = 0
        self._dir_caminho = {}
        self._dir_por_caminho = None
        self.gerado_em = 0.0

    # ---- construção -------------------------------------------------------
    def montar(self, nomes, pais, pasta, tam, data):
        """Ordena por nome e recalcula os pais para as posições novas."""
        baixos = [chave(n) for n in nomes]
        ordem = sorted(range(len(nomes)), key=baixos.__getitem__)
        nova = array("i", bytes(4 * len(ordem)))
        for novo, velho in enumerate(ordem):
            nova[velho] = novo
        with self.trava:
            self.nomes = [nomes[i] for i in ordem]
            self.baixos = [baixos[i] for i in ordem]
            self.pais = array("i", (nova[pais[i]] if pais[i] >= 0 else -1 for i in ordem))
            self.pasta = bytearray(pasta[i] for i in ordem)
            self.tam = array("q", (tam[i] for i in ordem))
            self.data = array("d", (data[i] for i in ordem))
            self._finalizar()

    def _finalizar(self):
        self.nbase = len(self.nomes)
        self.apagado = bytearray(self.nbase)
        self.blob = "\n" + "\n".join(self.baixos) + "\n"
        self.inicios = array("q", accumulate((len(s) + 1 for s in self.baixos), initial=1))
        self._dir_caminho = {}
        self._dir_por_caminho = None
        self.gerado_em = time.time()

    def salvar(self):
        os.makedirs(PASTA_DADOS, exist_ok=True)
        with self.trava:
            dados = (VERSAO_CACHE, self.gerado_em, self.nomes[:self.nbase],
                     self.pais[:self.nbase].tobytes(), bytes(self.pasta[:self.nbase]),
                     self.tam[:self.nbase].tobytes(), self.data[:self.nbase].tobytes())
        tmp = CACHE + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(dados, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, CACHE)

    def carregar(self):
        try:
            with open(CACHE, "rb") as f:
                versao, gerado, nomes, pais, pasta, tam, data = pickle.load(f)
        except (OSError, ValueError, pickle.UnpicklingError, EOFError):
            return False
        if versao != VERSAO_CACHE:
            return False
        with self.trava:
            self.nomes = nomes
            self.baixos = [chave(n) for n in nomes]
            self.pais = array("i"); self.pais.frombytes(pais)
            self.pasta = bytearray(pasta)
            self.tam = array("q"); self.tam.frombytes(tam)
            self.data = array("d"); self.data.frombytes(data)
            self._finalizar()
            self.gerado_em = gerado
        return True

    # ---- leitura ----------------------------------------------------------
    def __len__(self):
        return len(self.nomes)

    def caminho_pasta(self, i):
        """Caminho completo da pasta i (memoizado)."""
        c = self._dir_caminho.get(i)
        if c is not None:
            return c
        cadeia = []
        j = i
        while j >= 0 and j not in self._dir_caminho:
            cadeia.append(j)
            j = self.pais[j]
        base = self._dir_caminho.get(j, "") if j >= 0 else ""
        for k in reversed(cadeia):
            base = self.nomes[k] if self.pais[k] < 0 else base + "\\" + self.nomes[k]
            self._dir_caminho[k] = base
        return base

    def pasta_de(self, i):
        p = self.pais[i]
        return self.caminho_pasta(p) + ("\\" if self.pais[p] < 0 else "") if p >= 0 else ""

    def caminho(self, i):
        p = self.pais[i]
        if p < 0:
            return self.nomes[i] + "\\"
        return self.caminho_pasta(p) + "\\" + self.nomes[i]

    # ---- atualização ao vivo (eventos do sistema de arquivos) ------------
    def _mapa_pastas(self):
        if self._dir_por_caminho is None:
            m = {}
            for i in range(len(self.nomes)):
                if self.pasta[i] and not self.apagado[i]:
                    m[self.caminho_pasta(i).lower()] = i
            self._dir_por_caminho = m
        return self._dir_por_caminho

    def localizar(self, caminho):
        caminho = os.path.normpath(caminho)
        pai, nome = os.path.split(caminho)
        ip = self._mapa_pastas().get(pai.rstrip("\\").lower())
        if ip is None:
            return None, None
        nb = chave(nome)
        # a base está em ordem de chave: bisseção e depois os nomes iguais
        i = bisect_left(self.baixos, nb, 0, self.nbase)
        while i < self.nbase and self.baixos[i] == nb:
            if self.pais[i] == ip and not self.apagado[i]:
                return i, ip
            i += 1
        for i in range(self.nbase, len(self.nomes)):
            if self.baixos[i] == nb and self.pais[i] == ip and not self.apagado[i]:
                return i, ip
        return None, ip

    def criado(self, caminho):
        with self.trava:
            i, ip = self.localizar(caminho)
            if ip is None:
                return
            try:
                st = os.stat(caminho, follow_symlinks=False)
            except OSError:
                return
            d = os.path.isdir(caminho)
            if i is not None:
                self.tam[i] = -1 if d else st.st_size
                self.data[i] = st.st_mtime
                return
            nome = os.path.basename(os.path.normpath(caminho))
            self.nomes.append(nome); self.baixos.append(chave(nome))
            self.pais.append(ip); self.pasta.append(1 if d else 0)
            self.tam.append(-1 if d else st.st_size); self.data.append(st.st_mtime)
            self.apagado.append(0)
            if d:
                novo = len(self.nomes) - 1
                self._mapa_pastas()[os.path.normpath(caminho).lower()] = novo

    def removido(self, caminho):
        with self.trava:
            i, _ = self.localizar(caminho)
            if i is None:
                return
            self.apagado[i] = 1
            if self.pasta[i]:
                pc = os.path.normpath(caminho).lower()
                self._mapa_pastas().pop(pc, None)
                # filhos de pasta apagada: marca pelo prefixo do caminho
                pref = pc + "\\"
                for k, v in list(self._dir_por_caminho.items()):
                    if k.startswith(pref):
                        del self._dir_por_caminho[k]
                        self.apagado[v] = 1
                for j in range(len(self.nomes)):
                    if not self.apagado[j] and self.pais[j] >= 0 and self.apagado[self.pais[j]]:
                        self.apagado[j] = 1

    def alterado(self, caminho):
        with self.trava:
            i, _ = self.localizar(caminho)
            if i is None:
                return
            try:
                st = os.stat(caminho, follow_symlinks=False)
            except OSError:
                return
            if not self.pasta[i]:
                self.tam[i] = st.st_size
            self.data[i] = st.st_mtime
