"""Preferências, histórico e favoritos (JSON), e as pastas de dados do programa.

O índice de nomes fica em %LOCALAPPDATA% (é pequeno); o índice do conteúdo e o cache de OCR
ficam no disco fixo com mais espaço livre (fora do disco do Windows, quando há outro).
"""
import json
import os
import threading

PASTA_DADOS = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BuscadorLocal")


def _disco_com_espaco():
    """Pasta para os dados grandes: no disco fixo com mais espaço livre (C: se for ele)."""
    import ctypes
    import shutil
    import string
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    sistema = os.environ.get("SystemDrive", "C:").upper()
    fixos = [f"{l}:\\" for i, l in enumerate(string.ascii_uppercase)
             if mask >> i & 1 and k32.GetDriveTypeW(f"{l}:\\") == 3]
    # onde já há dados fica onde está, mesmo que outro disco passe a ter mais espaço
    if os.path.exists(os.path.join(PASTA_DADOS, "conteudo.db")):
        return PASTA_DADOS
    for raiz in fixos:
        if not raiz.upper().startswith(sistema) and os.path.isdir(os.path.join(raiz, "BuscadorLocal")):
            return os.path.join(raiz, "BuscadorLocal")
    melhor, livre = None, -1
    for raiz in fixos:
        try:
            f = shutil.disk_usage(raiz).free
        except OSError:
            continue
        if f > livre:
            melhor, livre = raiz, f
    if melhor is None or melhor.upper().startswith(sistema):
        return PASTA_DADOS
    return os.path.join(melhor, "BuscadorLocal")


PASTA_GRANDE = _disco_com_espaco()
ARQUIVO = os.path.join(PASTA_DADOS, "config.json")

PADRAO = {
    "historico": [],
    "favoritos": [],             # [{"nome": ..., "busca": ...}]
    "abertos": [],               # caminhos abertos pelo Buscador, mais recente primeiro
    "recentes_no_topo": True,
    "copiar_com_fonte": False,
    "indexar_conteudo": True,
    "conteudo_ignorar": [
        r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)", r"C:\ProgramData",
        r"\AppData\\", r"\$Recycle.Bin", r"\node_modules\\", r"\.venv\\", r"\site-packages\\",
        r"\.git\\", r"\BuscadorLocal\\",
    ],
    "conteudo_ext": ["pdf", "docx", "pptx", "txt", "md"],
}

_trava = threading.Lock()
_dados = None


def _carregar():
    global _dados
    if _dados is None:
        try:
            with open(ARQUIVO, encoding="utf-8") as f:
                _dados = {**PADRAO, **json.load(f)}
        except (OSError, ValueError):
            _dados = json.loads(json.dumps(PADRAO))
    return _dados


def get(chave):
    with _trava:
        return _carregar()[chave]


def set(chave, valor):
    with _trava:
        _carregar()[chave] = valor
        os.makedirs(PASTA_DADOS, exist_ok=True)
        tmp = ARQUIVO + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_dados, f, ensure_ascii=False, indent=1)
        os.replace(tmp, ARQUIVO)


def empilhar(chave, valor, limite):
    """Põe valor no começo da lista (sem repetir) e corta no limite."""
    lista = [v for v in get(chave) if v != valor]
    lista.insert(0, valor)
    set(chave, lista[:limite])
