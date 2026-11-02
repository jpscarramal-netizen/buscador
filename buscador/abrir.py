"""Abrir arquivo na página certa, recentes e Lixeira."""
import ctypes
import glob
import os
import pathlib
import subprocess
import time
from ctypes import wintypes

from . import config


def programa_padrao(ext):
    buf = ctypes.create_unicode_buffer(1024)
    n = wintypes.DWORD(1024)
    if ctypes.windll.shlwapi.AssocQueryStringW(0, 2, "." + ext, "open", buf, ctypes.byref(n)) == 0:
        return buf.value
    return None


def abrir(caminho, pagina=None):
    """Abre com o programa padrão; em PDF, já na página (Edge, Chrome, Acrobat, Sumatra, Foxit)."""
    registrar_aberto(caminho)
    if pagina and caminho.lower().endswith(".pdf"):
        exe = programa_padrao("pdf")
        nome = os.path.basename(exe or "").lower()
        args = None
        if nome in ("msedge.exe", "chrome.exe", "brave.exe", "firefox.exe", "opera.exe", "vivaldi.exe"):
            args = [exe, pathlib.Path(caminho).as_uri() + f"#page={pagina}"]
        elif nome in ("acrobat.exe", "acrord32.exe"):
            args = [exe, "/A", f"page={pagina}", caminho]
        elif nome.startswith("sumatrapdf"):
            args = [exe, "-page", str(pagina), caminho]
        elif nome.startswith("foxit"):
            args = [exe, caminho, "/A", f"page={pagina}"]
        if args:
            subprocess.Popen(args)
            return
    os.startfile(caminho)


def registrar_aberto(caminho):
    abertos = [a for a in config.get("abertos") if a[0] != caminho]
    abertos.insert(0, [caminho, time.time()])
    config.set("abertos", abertos[:300])


def recentes():
    """Caminhos abertos recentemente (pelo Buscador e pelo Windows), mais recente primeiro."""
    from PySide6.QtCore import QFileInfo
    itens = {c: t for c, t in config.get("abertos")}
    pasta = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Recent")
    for lnk in glob.glob(os.path.join(pasta, "*.lnk")):
        try:
            alvo = QFileInfo(lnk).symLinkTarget()
            t = os.path.getmtime(lnk)
        except OSError:
            continue
        if alvo and os.path.isfile(alvo):
            alvo = os.path.normpath(alvo)
            itens[alvo] = max(t, itens.get(alvo, 0))
    return [c for c, _ in sorted(itens.items(), key=lambda x: -x[1])]


def para_lixeira(caminhos):
    from send2trash import send2trash
    erros = []
    for c in caminhos:
        try:
            send2trash(c)
        except Exception as e:
            erros.append(f"{c}: {e}")
    return erros
