"""OCR de página escaneada com o OCR que já vem no Windows (pt-BR), sem programa extra.

Devolve as palavras com a caixa em coordenadas da página do PDF, no mesmo formato que o
PyMuPDF usa em get_text("words"): (x0, y0, x1, y1, texto, bloco, linha, n).
O resultado fica guardado em disco, ligado ao caminho, tamanho e data do arquivo.
"""
import asyncio
import hashlib
import json
import os

import pymupdf

from .config import PASTA_GRANDE
from .extrair import longo

PASTA_OCR = os.path.join(PASTA_GRANDE, "ocr")
_motor = None


def disponivel():
    try:
        return _engine() is not None
    except Exception:
        return False


def _engine():
    global _motor
    if _motor is None:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        _motor = OcrEngine.try_create_from_language(Language("pt-BR")) or OcrEngine.try_create_from_user_profile_languages()
    return _motor


def _chave(caminho):
    st = os.stat(longo(caminho))
    base = f"{os.path.normcase(caminho)}|{st.st_size}|{int(st.st_mtime)}"
    return os.path.join(PASTA_OCR, hashlib.sha1(base.encode("utf-8")).hexdigest() + ".json")


def carregar(caminho):
    """Palavras de OCR já feitas para este arquivo: {página: [palavras]} ou None."""
    try:
        with open(_chave(caminho), encoding="utf-8") as f:
            return {int(k): [tuple(w) for w in v] for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return None


def salvar(caminho, paginas):
    os.makedirs(PASTA_OCR, exist_ok=True)
    with open(_chave(caminho), "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in paginas.items()}, f, ensure_ascii=False)


def ocr_pagina(pagina):
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    eng = _engine()
    r = pagina.rect
    lado = max(r.width, r.height) or 1
    z = min(4.0, min(OcrEngine.max_image_dimension, 3000) / lado)
    pix = pagina.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csGRAY, alpha=False)
    dados = pix.samples if pix.stride == pix.width else b"".join(
        pix.samples[y * pix.stride:y * pix.stride + pix.width] for y in range(pix.height))
    w = DataWriter()
    w.write_bytes(bytes(dados))
    bmp = SoftwareBitmap.create_copy_from_buffer(w.detach_buffer(), BitmapPixelFormat.GRAY8, pix.width, pix.height)
    res = asyncio.run(_reconhecer(eng, bmp))
    palavras = []
    for nl, linha in enumerate(res.lines):
        for nw, p in enumerate(linha.words):
            b = p.bounding_rect
            palavras.append((b.x / z, b.y / z, (b.x + b.width) / z, (b.y + b.height) / z, p.text, 0, nl, nw))
    return palavras


async def _reconhecer(eng, bmp):
    return await eng.recognize_async(bmp)


def texto_das_palavras(palavras):
    linhas, chave = [], None
    for w in palavras:
        if w[6] != chave:
            linhas.append([])
            chave = w[6]
        linhas[-1].append(w[4])
    return "\n".join(" ".join(l) for l in linhas)
