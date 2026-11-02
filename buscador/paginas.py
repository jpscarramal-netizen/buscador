"""Visor de PDF: páginas empilhadas como num leitor, com seleção de texto por arraste e Ctrl+C.

Também recorta uma área da página como imagem (modo Recortar ou Shift + arrastar) e usa as
palavras do OCR nas páginas escaneadas.
"""
import os
import threading
from bisect import bisect_right
from collections import OrderedDict

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QScrollArea, QWidget

from . import config
from .emenda import juntar
from .extrair import longo

MARGEM = 8
VAO = 10
MAX_CACHE = 12
COR_SEL = QColor(0, 120, 215, 90)
COR_ACHADO = QColor(255, 210, 0, 110)
COR_ATUAL = QColor(255, 140, 0, 150)
ZOOM_RECORTE = 3          # imagem recortada sai com 3x a resolução do PDF (216 dpi)


class _Folha(QWidget):
    def __init__(self, visor):
        super().__init__()
        self.v = visor
        self.setMouseTracking(True)
        self.setCursor(Qt.IBeamCursor)
        self.setFocusPolicy(Qt.ClickFocus)

    def paintEvent(self, e):
        p = QPainter(self)
        self.v._pintar(p, QRectF(e.rect()))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self.v.recortando or e.modifiers() & Qt.ShiftModifier:
                self.v.rec_a = self.v.rec_b = e.position()
                self.update()
                return
            alvo = self.v._palavra_em(e.position())
            self.v.sel_a = self.v.sel_b = alvo
            self.update()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton and self.v.rec_a is not None:
            self.v.rec_b = e.position()
            self.update()
            self.v.ensureVisible(int(e.position().x()), int(e.position().y()), 0, 30)
            return
        if e.buttons() & Qt.LeftButton and self.v.sel_a is not None:
            alvo = self.v._palavra_em(e.position())
            if alvo is not None:
                self.v.sel_b = alvo
                self.update()
            self.v.ensureVisible(int(e.position().x()), int(e.position().y()), 0, 30)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.v.rec_a is not None:
            self.v._recortar(self.v.rec_a, e.position())
            self.v.rec_a = self.v.rec_b = None
            self.update()

    def mouseDoubleClickEvent(self, e):
        alvo = self.v._palavra_em(e.position(), perto=False)
        if alvo is not None:
            self.v.sel_a = self.v.sel_b = alvo
            self.update()

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Copy):
            self.v.copiar()
        elif e.matches(QKeySequence.SelectAll):
            self.v.selecionar_tudo()
        else:
            super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        a = m.addAction("Copiar", self.v.copiar)
        a.setEnabled(self.v.tem_selecao())
        k = self.v._pagina_em_y(e.pos().y())
        m.addAction(f"Copiar texto da página {k + 1}", lambda: self.v.copiar_pagina(k))
        m.addAction("Selecionar tudo", self.v.selecionar_tudo)
        m.addSeparator()
        m.addAction(f"Copiar a página {k + 1} como imagem", lambda: self.v.copiar_imagem_pagina(k))
        m.addAction("Recortar uma área como imagem (Shift + arrastar)", lambda: self.v.modoRecorte.emit(True))
        m.exec(e.globalPos())


class VisorPdf(QScrollArea):
    paginaMudou = Signal(int)
    copiado = Signal(int)          # número de caracteres
    recortado = Signal(int, int)   # largura e altura da imagem copiada
    modoRecorte = Signal(bool)
    resultado = Signal(bool)       # a busca no visor achou (ou não) o termo
    _achou = Signal(int, int, int, object)   # geração, página (-1 = nada), índice, caixas

    def __init__(self):
        super().__init__()
        self.folha = _Folha(self)
        self.setWidget(self.folha)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: #525659; border: none; }")
        self.folha.setStyleSheet("background: #525659;")
        self.doc = None
        self.nome = ""             # nome do arquivo, para a fonte na cópia
        self.recortando = False
        self.geracao = 0           # muda a cada busca nova; a thread antiga para sozinha
        self._achou.connect(self._chegou_achado)
        self.t_layout = QTimer(singleShot=True, interval=80, timeout=self._layout)
        self.verticalScrollBar().valueChanged.connect(self._rolou)
        self._limpar_estado()

    def _limpar_estado(self):
        self.tops, self.alturas, self.escalas, self.larguras = [], [], [], []
        self.pix = OrderedDict()
        self.palavras = {}
        self.achados = {}
        self.termo = ""
        self.atual = None          # (página, i) do achado em foco
        self.sel_a = self.sel_b = None
        self.rec_a = self.rec_b = None
        self.ocr = {}              # página -> palavras do OCR (páginas escaneadas)
        self.pagina = 0

    # ---- documento e layout ----------------------------------------------
    def abrir(self, doc, nome=""):
        self.doc = doc
        self.nome = nome
        self._limpar_estado()
        self._layout()
        self.verticalScrollBar().setValue(0)

    def definir_recorte(self, ligado):
        self.recortando = ligado
        self.folha.setCursor(Qt.CrossCursor if ligado else Qt.IBeamCursor)

    def definir_ocr(self, paginas):
        self.ocr = dict(paginas)
        for k in self.ocr:
            self.palavras.pop(k, None)
        self.folha.update()

    def fechar(self):
        self.geracao += 1
        self.doc = None
        self._limpar_estado()
        self.folha.resize(1, 1)

    def _layout(self):
        if self.doc is None:
            return
        frac = self._posicao_relativa()
        largura = max(200, self.viewport().width())
        disp = largura - 2 * MARGEM
        self.tops, self.alturas, self.escalas, self.larguras = [], [], [], []
        y = MARGEM
        for pg in self.doc:
            r = pg.rect
            esc = disp / r.width if r.width else 1
            self.tops.append(y)
            self.escalas.append(esc)
            self.larguras.append(r.width * esc)
            self.alturas.append(r.height * esc)
            y += r.height * esc + VAO
        self.pix.clear()
        self.folha.resize(largura, int(y + MARGEM))
        if frac is not None:
            k, f = frac
            self.verticalScrollBar().setValue(int(self.tops[k] + f * self.alturas[k]))
        self.folha.update()

    def _posicao_relativa(self):
        if not self.tops:
            return None
        y = self.verticalScrollBar().value()
        k = self._pagina_em_y(y)
        return k, (y - self.tops[k]) / max(1, self.alturas[k])

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.doc is not None and abs(self.viewport().width() - self.folha.width()) > 2:
            self.t_layout.start()

    def _pagina_em_y(self, y):
        return max(0, min(len(self.tops) - 1, bisect_right(self.tops, y) - 1)) if self.tops else 0

    def _rolou(self, v):
        k = self._pagina_em_y(v + self.viewport().height() // 3)
        if k != self.pagina:
            self.pagina = k
            self.paginaMudou.emit(k)

    def ir(self, k):
        if self.doc is not None and 0 <= k < len(self.tops):
            self.verticalScrollBar().setValue(int(self.tops[k] - MARGEM))

    # ---- desenho ----------------------------------------------------------
    def _pixmap(self, k):
        pm = self.pix.get(k)
        if pm is not None:
            self.pix.move_to_end(k)
            return pm
        dpr = self.devicePixelRatioF()
        z = self.escalas[k] * dpr
        try:
            p = self.doc[k].get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
            img = QImage(p.samples, p.width, p.height, p.stride, QImage.Format_RGB888).copy()
            pm = QPixmap.fromImage(img)
        except Exception:
            pm = QPixmap(int(self.larguras[k] * dpr), int(self.alturas[k] * dpr))
            pm.fill(Qt.white)
        pm.setDevicePixelRatio(dpr)
        self.pix[k] = pm
        while len(self.pix) > MAX_CACHE:
            self.pix.popitem(last=False)
        return pm

    def _pintar(self, p, area):
        if self.doc is None:
            return
        k = self._pagina_em_y(area.top())
        ini, fim = self._ordem_sel()
        while k < len(self.tops) and self.tops[k] < area.bottom():
            alvo = QRectF(MARGEM, self.tops[k], self.larguras[k], self.alturas[k])
            pm = self._pixmap(k)
            p.drawPixmap(alvo, pm, QRectF(pm.rect()))
            if self.termo:
                for i, r in enumerate(self._achados(k)):
                    p.fillRect(self._tela(k, r), COR_ATUAL if self.atual == (k, i) else COR_ACHADO)
            if ini is not None and ini[0] <= k <= fim[0]:
                pal = self._palavras(k)
                a = ini[1] if ini[0] == k else 0
                b = fim[1] if fim[0] == k else len(pal) - 1
                for w in pal[a:b + 1]:
                    p.fillRect(self._tela(k, w[0]), COR_SEL)
            k += 1
        if self.rec_a is not None and self.rec_b is not None:
            r = QRectF(self.rec_a, self.rec_b).normalized()
            p.fillRect(r, QColor(0, 120, 215, 40))
            p.setPen(QPen(QColor(0, 120, 215), 1.5, Qt.DashLine))
            p.drawRect(r)

    def _tela(self, k, r):
        e = self.escalas[k]
        return QRectF(MARGEM + r.x() * e, self.tops[k] + r.y() * e, r.width() * e, r.height() * e)

    # ---- palavras e seleção ----------------------------------------------
    def _palavras(self, k):
        pal = self.palavras.get(k)
        if pal is None:
            try:
                bruto = self.doc[k].get_text("words", sort=True)
            except Exception:
                bruto = []
            if not bruto and k in self.ocr:
                bruto = self.ocr[k]
            pal = [(QRectF(w[0], w[1], w[2] - w[0], w[3] - w[1]), w[4], w[5], w[6]) for w in bruto]
            self.palavras[k] = pal
        return pal

    def _palavra_em(self, pt, perto=True):
        if not self.tops:
            return None
        k = self._pagina_em_y(pt.y())
        pal = self._palavras(k)
        if not pal:
            return None
        e = self.escalas[k]
        x, y = (pt.x() - MARGEM) / e, (pt.y() - self.tops[k]) / e
        melhor, dist = None, None
        for i, w in enumerate(pal):
            r = w[0]
            if r.contains(QPointF(x, y)):
                return k, i
            if perto:
                dx = max(r.left() - x, 0, x - r.right())
                dy = max(r.top() - y, 0, y - r.bottom())
                d = dy * 4 + dx
                if dist is None or d < dist:
                    melhor, dist = i, d
        return (k, melhor) if melhor is not None else None

    def _ordem_sel(self):
        if self.sel_a is None or self.sel_b is None:
            return None, None
        return (self.sel_a, self.sel_b) if self.sel_a <= self.sel_b else (self.sel_b, self.sel_a)

    def tem_selecao(self):
        return self.sel_a is not None

    def _juntar(self, ini, fim):
        """Palavras da seleção viram linhas (por linha do PDF) e as linhas se juntam pela regra de frases."""
        linhas, chave = [], None
        for k in range(ini[0], fim[0] + 1):
            pal = self._palavras(k)
            a = ini[1] if k == ini[0] else 0
            b = fim[1] if k == fim[0] else len(pal) - 1
            for w in pal[a:b + 1]:
                c = (k, w[2], w[3])
                if c != chave:
                    linhas.append([])
                    chave = c
                linhas[-1].append(w[1])
        return juntar((" ".join(l) for l in linhas), "\n")

    def texto_selecionado(self):
        ini, fim = self._ordem_sel()
        return self._juntar(ini, fim) if ini is not None else ""

    def com_fonte(self, texto, pag_a, pag_b):
        """Acrescenta "(Fonte: arquivo, p. X)" quando a opção está ligada."""
        if not config.get("copiar_com_fonte") or not self.nome:
            return texto
        pags = f"p. {pag_a + 1}" if pag_a == pag_b else f"p. {pag_a + 1}-{pag_b + 1}"
        return f"{texto}\n\n(Fonte: {os.path.splitext(self.nome)[0]}, {pags})"

    def copiar(self):
        ini, fim = self._ordem_sel()
        t = self.texto_selecionado()
        if t:
            QGuiApplication.clipboard().setText(self.com_fonte(t, ini[0], fim[0]))
            self.copiado.emit(len(t))

    def copiar_pagina(self, k):
        pal = self._palavras(k)
        if pal:
            t = self._juntar((k, 0), (k, len(pal) - 1))
            QGuiApplication.clipboard().setText(self.com_fonte(t, k, k))
            self.copiado.emit(len(t))

    # ---- recorte como imagem ---------------------------------------------
    def _imagem(self, k, clip=None):
        z = ZOOM_RECORTE
        p = self.doc[k].get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False)
        return QImage(p.samples, p.width, p.height, p.stride, QImage.Format_RGB888).copy()

    def copiar_imagem_pagina(self, k):
        img = self._imagem(k)
        QGuiApplication.clipboard().setImage(img)
        self.recortado.emit(img.width(), img.height())

    def _recortar(self, a, b):
        """Copia a área arrastada (dentro de uma página) como imagem em alta resolução."""
        if self.doc is None:
            return
        r = QRectF(a, b).normalized()
        if r.width() < 6 or r.height() < 6:
            return
        k = self._pagina_em_y(r.center().y())
        e = self.escalas[k]
        x0 = max(0, (r.left() - MARGEM) / e)
        y0 = max(0, (r.top() - self.tops[k]) / e)
        x1 = min(self.doc[k].rect.width, (r.right() - MARGEM) / e)
        y1 = min(self.doc[k].rect.height, (r.bottom() - self.tops[k]) / e)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        img = self._imagem(k, pymupdf.Rect(x0, y0, x1, y1))
        QGuiApplication.clipboard().setImage(img)
        self.recortado.emit(img.width(), img.height())

    def selecionar_tudo(self):
        if self.doc is None:
            return
        ult = len(self.tops) - 1
        while ult >= 0 and not self._palavras(ult):
            ult -= 1
        prim = 0
        while prim <= ult and not self._palavras(prim):
            prim += 1
        if prim <= ult:
            self.sel_a, self.sel_b = (prim, 0), (ult, len(self._palavras(ult)) - 1)
            self.folha.update()

    # ---- procurar ----------------------------------------------------------
    def _achados(self, k):
        r = self.achados.get(k)
        if r is None:
            try:
                r = [QRectF(q.x0, q.y0, q.width, q.height) for q in self.doc[k].search_for(self.termo)]
            except Exception:
                r = []
            self.achados[k] = r
        return r

    def procurar(self, termo):
        """Procura em segundo plano a partir da página visível; o resultado chega por 'resultado'."""
        self.termo = termo.strip()
        self.achados = {}
        self.atual = None
        self.geracao += 1
        if self.termo and self.doc is not None:
            self._seguinte(self.pagina, -1)
        self.folha.update()

    def proximo(self):
        if not self.termo or self.doc is None:
            return
        k, i = self.atual if self.atual else (self.pagina, -1)
        hits = self.achados.get(k)
        if hits is not None and i + 1 < len(hits):
            self._focar(k, i + 1)
            return
        self.geracao += 1
        self._seguinte(k, i)

    def _seguinte(self, k0, i0):
        """Varre as páginas numa thread com documento próprio (o PyMuPDF não é seguro entre threads)."""
        g, termo, n, caminho = self.geracao, self.termo, len(self.tops), self.doc.name

        def tarefa():
            try:
                doc = pymupdf.open(longo(caminho))
            except Exception:
                return
            with doc:
                for passo in range(n + 1):
                    if g != self.geracao:
                        return
                    k = (k0 + passo) % n
                    hits = [(q.x0, q.y0, q.width, q.height) for q in doc[k].search_for(termo)]
                    ini = i0 + 1 if passo == 0 else 0
                    if ini < len(hits):
                        self._achou.emit(g, k, ini, hits)
                        return
            self._achou.emit(g, -1, 0, None)

        threading.Thread(target=tarefa, daemon=True).start()

    def _chegou_achado(self, g, k, ini, hits):
        if g != self.geracao:
            return
        if k < 0:
            self.atual = None
            self.folha.update()
            self.resultado.emit(False)
            return
        self.achados[k] = [QRectF(*h) for h in hits]
        self._focar(k, ini)
        self.resultado.emit(True)

    def _focar(self, k, i):
        self.atual = (k, i)
        r = self._tela(k, self.achados[k][i])
        self.ensureVisible(int(r.center().x()), int(r.center().y()), 0, self.viewport().height() // 3)
        self.folha.update()
