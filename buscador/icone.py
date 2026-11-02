"""Ícone do Buscador: folha de documento com lupa, sobre um quadrado laranja arredondado.

Desenhado em código (sem arquivo de imagem). `python -m buscador.icone` grava o buscador.ico
com todos os tamanhos que o Windows usa.
"""
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

TAMANHOS = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def desenhar(t):
    img = QImage(t, t, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(t / 256, t / 256)

    # fundo: quadrado laranja arredondado com degradê
    g = QLinearGradient(0, 0, 0, 256)
    g.setColorAt(0, QColor("#ff8a3d"))
    g.setColorAt(1, QColor("#d9480f"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 52, 52)

    pequeno = t <= 24
    if not pequeno:
        # folha com o canto dobrado e linhas de texto
        folha = QPainterPath()
        folha.moveTo(56, 40)
        folha.lineTo(150, 40)
        folha.lineTo(188, 78)
        folha.lineTo(188, 206)
        folha.quadTo(188, 216, 178, 216)
        folha.lineTo(66, 216)
        folha.quadTo(56, 216, 56, 206)
        folha.closeSubpath()
        p.setBrush(QColor("#fff7f0"))
        p.drawPath(folha)
        dobra = QPainterPath()
        dobra.moveTo(150, 40)
        dobra.lineTo(150, 78)
        dobra.lineTo(188, 78)
        dobra.closeSubpath()
        p.setBrush(QColor("#ffd0ad"))
        p.drawPath(dobra)
        p.setPen(QPen(QColor("#f0a070"), 10, Qt.SolidLine, Qt.RoundCap))
        for y, x1 in ((96, 166), (122, 166), (148, 128)):
            p.drawLine(QPointF(78, y), QPointF(x1, y))

    # lupa (maior quando o ícone é pequeno, para continuar legível)
    cx, cy, r, larg = (112, 112, 62, 30) if pequeno else (150, 150, 44, 16)
    fim = (226, 226) if pequeno else (214, 214)
    p.setPen(QPen(QColor("#5c2a0a"), larg * 1.25, Qt.SolidLine, Qt.RoundCap))
    d = r * 0.72
    p.drawLine(QPointF(cx + d, cy + d), QPointF(*fim))
    # aro escuro sobre a folha; nos tamanhos pequenos (sem folha) o aro branco aparece melhor
    p.setPen(QPen(QColor("#ffffff") if pequeno else QColor("#6b2e0b"), larg))
    p.setBrush(Qt.NoBrush if pequeno else QColor(255, 196, 150, 170))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.end()
    return img


def icone_qt():
    ic = QIcon()
    for t in TAMANHOS:
        ic.addPixmap(QPixmap.fromImage(desenhar(t)))
    return ic


def gravar_ico(caminho):
    """ICO com uma imagem PNG para cada tamanho (formato aceito pelo Windows Vista em diante)."""
    pngs = []
    for t in TAMANHOS:
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.WriteOnly)
        desenhar(t).save(buf, "PNG")
        pngs.append((t, bytes(ba)))
    cab = struct.pack("<HHH", 0, 1, len(pngs))
    pos = 6 + 16 * len(pngs)
    entradas, dados = b"", b""
    for t, png in pngs:
        entradas += struct.pack("<BBBBHHII", t % 256, t % 256, 0, 0, 1, 32, len(png), pos + len(dados))
        dados += png
    with open(caminho, "wb") as f:
        f.write(cab + entradas + dados)


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    gravar_ico(sys.argv[1] if len(sys.argv) > 1 else "buscador.ico")
    desenhar(256).save("icone-256.png")
