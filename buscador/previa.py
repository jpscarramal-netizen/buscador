"""Painel de pré-visualização: PDF com texto selecionável e copiável, Word, PowerPoint, Excel,
texto puro e imagem. Recorta área da página como imagem e faz OCR de PDF escaneado."""
import os
import re
import threading

import pymupdf
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPixmap, QShortcut, QTextCharFormat
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
                               QStackedWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from . import config, ocr
from .extrair import EXT_DOC, EXT_IMAGEM, EXT_OFFICE, EXT_TEXTO, ext, longo, office, paragrafos, texto_puro
from .paginas import VisorPdf

MARCA = re.compile(r"── (?:Página|Slide) (\d+) ──")


class _Ponte(QObject):
    texto = Signal(int, str, int, int)     # pedido, texto, páginas lidas, total
    fim = Signal(int, str, int)            # pedido, aviso, páginas sem texto
    ocr = Signal(int, int, int, object)    # pedido, feitas, total, {página: palavras} (None = em andamento)
    ocr_erro = Signal(int, str)            # pedido, motivo


class Previa(QWidget):
    ocrFeito = Signal(str, object)          # caminho, {página: palavras}

    def __init__(self):
        super().__init__()
        self.caminho = None
        self.pedido = 0
        self.doc = None
        self.pagina = 0
        self.paginas_texto = []
        self.sem_texto = 0
        self.ponte = _Ponte()
        self.ponte.texto.connect(self._chegou_texto)
        self.ponte.fim.connect(self._fim_texto)
        self.ponte.ocr.connect(self._progresso_ocr)
        self.ponte.ocr_erro.connect(self._erro_ocr)

        self.titulo = QLabel()
        self.titulo.setWordWrap(True)
        self.titulo.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.titulo.setStyleSheet("font-weight:600; padding:4px 2px")

        self.bt_pagina = QToolButton(text="Página", checkable=True, checked=True)
        self.bt_texto = QToolButton(text="Só texto", checkable=True)
        self.bt_texto.clicked.connect(lambda: self._modo(0))
        self.bt_pagina.clicked.connect(lambda: self._modo(1))
        self.bt_ant = QToolButton(text="◀")
        self.bt_prox = QToolButton(text="▶")
        self.bt_ant.clicked.connect(lambda: self._ir(self.pagina - 1))
        self.bt_prox.clicked.connect(lambda: self._ir(self.pagina + 1))
        self.lb_pag = QLabel()
        self.bt_recorte = QToolButton(text="✂ Recortar", checkable=True)
        self.bt_recorte.setToolTip("Arraste sobre a página para copiar a área como imagem (ou Shift + arrastar)")
        self.bt_recorte.toggled.connect(self._recorte)
        self.cb_fonte = QCheckBox("Com fonte")
        self.cb_fonte.setToolTip('O texto copiado leva "(Fonte: arquivo, p. 12)" no final')
        self.cb_fonte.setChecked(config.get("copiar_com_fonte"))
        self.cb_fonte.toggled.connect(lambda v: config.set("copiar_com_fonte", v))
        self.bt_ocr = QPushButton("Ler com OCR")
        self.bt_ocr.setToolTip("Reconhece o texto das páginas escaneadas com o OCR do Windows")
        self.bt_ocr.clicked.connect(self.fazer_ocr)
        self.bt_copiar = QPushButton("Copiar todo o texto")
        self.bt_copiar.clicked.connect(self.copiar_tudo)
        barra = QHBoxLayout()
        barra.setContentsMargins(0, 0, 0, 0)
        for w in (self.bt_pagina, self.bt_texto, self.bt_ant, self.lb_pag, self.bt_prox, self.bt_recorte):
            barra.addWidget(w)
        barra.addStretch(1)
        for w in (self.bt_ocr, self.cb_fonte, self.bt_copiar):
            barra.addWidget(w)
        self.barra = QWidget()
        self.barra.setLayout(barra)

        self.procura = QLineEdit(placeholderText="Procurar no texto (Enter = próximo)")
        # espera a pessoa parar de digitar antes de procurar (PDF grande tem centenas de páginas)
        self.t_procura = QTimer(self, singleShot=True, interval=300,
                                timeout=lambda: self._procurar(self.procura.text()))
        self.procura.textChanged.connect(lambda: self.t_procura.start())
        self.procura.returnPressed.connect(self._proximo)

        self.texto = QPlainTextEdit(readOnly=True)
        self.texto.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.texto.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.texto.installEventFilter(self)
        f = self.texto.font()
        f.setPointSize(10)
        self.texto.setFont(f)

        self.img = QLabel(alignment=Qt.AlignHCenter | Qt.AlignTop)
        self.rolagem = QScrollArea(widgetResizable=True)
        self.rolagem.setWidget(self.img)

        self.vazio = QLabel("Selecione um arquivo para ver a prévia.", alignment=Qt.AlignCenter)
        self.vazio.setWordWrap(True)
        self.vazio.setStyleSheet("color:#777")

        self.visor = VisorPdf()
        self.visor.paginaMudou.connect(self._pagina_visivel)
        self.visor.copiado.connect(lambda n: self._aviso(f"✓ {n:,} caracteres copiados".replace(",", ".")))
        self.visor.recortado.connect(lambda w, h: self._aviso(f"✓ Imagem copiada ({w}×{h}); cole no Anki, Word ou WhatsApp"))
        self.visor.modoRecorte.connect(self.bt_recorte.setChecked)
        self.visor.resultado.connect(self._resultado_visor)

        self.pilha = QStackedWidget()
        for w in (self.texto, self.rolagem, self.visor, self.vazio):
            self.pilha.addWidget(w)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        lay.addWidget(self.titulo)
        lay.addWidget(self.barra)
        lay.addWidget(self.procura)
        lay.addWidget(self.pilha, 1)
        QShortcut(QKeySequence.Find, self, activated=lambda: self.procura.setFocus())
        self.limpar()

    # ---- estado -----------------------------------------------------------
    def limpar(self, aviso="Selecione um arquivo para ver a prévia."):
        self.pedido += 1
        self.caminho = None
        self._fechar_doc()
        self.titulo.setText("")
        self.barra.hide()
        self.procura.hide()
        self.bt_ocr.hide()
        self.vazio.setText(aviso)
        self.pilha.setCurrentWidget(self.vazio)

    def _fechar_doc(self):
        self.visor.fechar()
        if self.doc is not None:
            self.doc.close()
            self.doc = None

    def _barra(self, paginas, recorte):
        """Mostra a barra com os botões que fazem sentido para o tipo de arquivo."""
        self.barra.show()
        self.procura.show()
        for w in (self.bt_texto, self.bt_pagina, self.bt_ant, self.bt_prox, self.lb_pag):
            w.setVisible(paginas)
        self.bt_recorte.setVisible(recorte)

    def mostrar(self, caminho, pagina=None, achar=None, recarregar=False):
        """Abre a prévia; pagina (1 = primeira) e achar vêm da busca no conteúdo."""
        if caminho == self.caminho and not recarregar:
            if pagina:
                self._ir(pagina - 1)
            if achar is not None and achar != self.procura.text():
                self.procura.setText(achar)
            return
        self.limpar()
        self.caminho = caminho
        self.titulo.setText(os.path.basename(caminho))
        e = ext(caminho)
        if os.path.isdir(longo(caminho)):
            self.vazio.setText("Pasta")
        elif e in EXT_DOC:
            self._abrir_doc(caminho)
        elif e in EXT_TEXTO:
            self._abrir_texto(caminho)
        elif e in EXT_OFFICE:
            self._abrir_office(caminho)
        elif e in EXT_IMAGEM:
            pm = QPixmap(longo(caminho))
            if pm.isNull():
                self.vazio.setText("Não consegui abrir a imagem.")
            else:
                larg = self.rolagem.viewport().width()
                self.img.setPixmap(pm.scaledToWidth(max(200, larg - 4), Qt.SmoothTransformation)
                                   if pm.width() > larg else pm)
                self.pilha.setCurrentWidget(self.rolagem)
        elif e in ("doc", "ppt", "xls", "odt", "rtf"):
            self.vazio.setText("Formato antigo do Office: sem prévia. Salve como .docx/.pptx/.xlsx para ver aqui.")
        else:
            self.vazio.setText("Sem prévia para este tipo de arquivo.")
        if pagina:
            self._ir(pagina - 1)
        if achar:
            self.procura.setText(achar)

    # ---- texto puro e Office ----------------------------------------------
    def _abrir_texto(self, caminho):
        try:
            s = texto_puro(caminho)
        except OSError as e:
            self.vazio.setText(f"Não consegui ler: {e.strerror}")
            return
        self.paginas_texto = [s]
        self.texto.setPlainText(s)
        self._barra(False, False)
        self.pilha.setCurrentWidget(self.texto)

    def _abrir_office(self, caminho):
        self._barra(False, False)
        self.paginas_texto = []
        self.texto.setPlainText("Lendo o arquivo…")
        self.pilha.setCurrentWidget(self.texto)
        pedido = self.pedido

        def tarefa():
            try:
                partes = office(caminho)
            except Exception as e:
                self.ponte.fim.emit(pedido, f"Não consegui ler: {e}", 0)
                return
            txt = "\n".join((f"── {r} ──\n{t}\n" if r else t) for r, t in partes)
            self.ponte.texto.emit(pedido, txt, len(partes), len(partes))
            self.ponte.fim.emit(pedido, "" if txt.strip() else "Arquivo sem texto.", 0)

        threading.Thread(target=tarefa, daemon=True).start()

    # ---- PDF e afins ------------------------------------------------------
    def _abrir_doc(self, caminho):
        try:
            self.doc = pymupdf.open(longo(caminho))
            if self.doc.needs_pass:
                self.limpar("PDF protegido por senha.")
                return
        except Exception as e:  # PDF corrompido, sem permissão etc.
            self.limpar(f"Não consegui abrir: {e}")
            return
        self._barra(True, True)
        self.pagina = 0
        self.paginas_texto = []
        self.texto.setPlainText("Lendo o texto…")
        self.visor.abrir(self.doc, os.path.basename(caminho))
        feito = ocr.carregar(caminho) if caminho.lower().endswith(".pdf") else None
        if feito:
            self.visor.definir_ocr(feito)
        self._modo(1 if self.bt_pagina.isChecked() else 0)
        self._atualizar_rotulo()
        pedido = self.pedido
        threading.Thread(target=self._extrair, args=(caminho, pedido, feito or {}), daemon=True).start()

    def _extrair(self, caminho, pedido, feito):
        """Lê o texto numa thread com documento próprio (o PyMuPDF não é seguro entre threads)."""
        try:
            doc = pymupdf.open(longo(caminho))
        except Exception as e:
            self.ponte.fim.emit(pedido, f"Não consegui ler o texto: {e}", 0)
            return
        total = doc.page_count
        lote = []
        sem_texto = so_imagem = 0         # so_imagem: o OCR já passou e não achou texto
        for k in range(total):
            if pedido != self.pedido:
                doc.close()
                return
            try:
                t = paragrafos(doc[k]).strip()
            except Exception:
                t = ""
            if not t and k in feito:
                t = ocr.texto_das_palavras(feito[k])
                so_imagem += not t
            elif not t:
                sem_texto += 1
            lote.append(f"── Página {k + 1} ──\n{t}\n")
            if len(lote) == 10 or k == total - 1:
                self.ponte.texto.emit(pedido, "\n".join(lote), k + 1, total)
                lote = []
        doc.close()
        aviso = ""
        if total and sem_texto == total:
            aviso = "Este arquivo não tem texto digital (é imagem escaneada). Use \"Ler com OCR\"."
        elif sem_texto:
            aviso = f"{sem_texto} de {total} páginas sem texto digital (imagem). \"Ler com OCR\" resolve."
        elif so_imagem:
            aviso = f"{so_imagem} página(s) só com figura, sem texto nem no OCR."
        self.ponte.fim.emit(pedido, aviso, sem_texto)

    def _chegou_texto(self, pedido, trecho, lidas, total):
        if pedido != self.pedido:
            return
        if not self.paginas_texto:
            self.texto.setPlainText("")
        self.paginas_texto.append(trecho)
        cur = self.texto.textCursor()
        barra = self.texto.verticalScrollBar().value()
        self.texto.appendPlainText(trecho)
        self.texto.setTextCursor(cur)
        self.texto.verticalScrollBar().setValue(barra)
        self.lb_pag.setToolTip(f"Texto lido: {lidas} de {total} páginas")

    def _fim_texto(self, pedido, aviso, sem_texto):
        if pedido != self.pedido:
            return
        self.sem_texto = sem_texto
        self.bt_ocr.setVisible(bool(sem_texto) and self.doc is not None and ocr.disponivel())
        self.bt_ocr.setText(f"Ler com OCR ({sem_texto} pág.)")
        if aviso:
            self.titulo.setText(f"{os.path.basename(self.caminho)}\n⚠ {aviso}")
        if self.procura.text():
            self._procurar(self.procura.text())

    # ---- OCR --------------------------------------------------------------
    def fazer_ocr(self):
        if self.doc is None or not self.caminho:
            return
        caminho, pedido = self.caminho, self.pedido
        self.bt_ocr.setEnabled(False)

        def tarefa():
            feito = ocr.carregar(caminho) or {}
            try:
                doc = pymupdf.open(longo(caminho))
            except Exception as e:
                self.ponte.ocr_erro.emit(pedido, str(e))
                return
            faltam = [k for k in range(doc.page_count)
                      if k not in feito and not doc[k].get_text("text").strip()]
            for n, k in enumerate(faltam, 1):
                if pedido != self.pedido:
                    break
                try:
                    feito[k] = ocr.ocr_pagina(doc[k])
                except Exception:
                    feito[k] = []
                self.ponte.ocr.emit(pedido, n, len(faltam), None)
                if n % 10 == 0:
                    ocr.salvar(caminho, feito)
            doc.close()
            ocr.salvar(caminho, feito)
            self.ocrFeito.emit(caminho, feito)
            self.ponte.ocr.emit(pedido, len(faltam), len(faltam), feito)

        threading.Thread(target=tarefa, daemon=True).start()

    def _progresso_ocr(self, pedido, feitas, total, resultado):
        if pedido != self.pedido:
            return
        if resultado is None:
            self.bt_ocr.setText(f"OCR {feitas}/{total}…")
            return
        self.bt_ocr.setEnabled(True)
        self.bt_ocr.hide()
        self.visor.definir_ocr(resultado)
        self.mostrar(self.caminho, self.pagina + 1, recarregar=True)   # relê o texto já com o OCR
        self._aviso(f"✓ OCR feito em {total} página(s); o texto já pode ser selecionado e copiado")

    def _erro_ocr(self, pedido, motivo):
        if pedido != self.pedido:
            return
        self.bt_ocr.setEnabled(True)
        self._aviso(f"⚠ O OCR não conseguiu abrir o arquivo: {motivo}")

    # ---- navegação --------------------------------------------------------
    def _modo(self, m):
        self.bt_texto.setChecked(m == 0)
        self.bt_pagina.setChecked(m == 1)
        if self.doc is None:
            return
        if m == 0:
            self.bt_recorte.setChecked(False)
            self.pilha.setCurrentWidget(self.texto)
            self._ir(self.pagina)
        else:
            self.pilha.setCurrentWidget(self.visor)
            self.visor.ir(self.pagina)
        self.bt_recorte.setVisible(m == 1)
        if self.procura.text():
            self._procurar(self.procura.text())

    def _recorte(self, ligado):
        self.visor.definir_recorte(ligado)
        if ligado:
            self._aviso("Arraste sobre a página para copiar a área como imagem")

    def _ir(self, p):
        if self.doc is None or not 0 <= p < self.doc.page_count:
            return
        self.pagina = p
        self._atualizar_rotulo()
        if self.bt_pagina.isChecked():
            self.visor.ir(p)
        else:
            achou = self.texto.document().find(f"── Página {p + 1} ──")
            if not achou.isNull():
                self.texto.setTextCursor(achou)
                self.texto.centerCursor()

    def _atualizar_rotulo(self):
        if self.doc is not None:
            self.lb_pag.setText(f"{self.pagina + 1} / {self.doc.page_count}")

    def _pagina_visivel(self, k):
        self.pagina = k
        self._atualizar_rotulo()

    def _aviso(self, txt):
        self.titulo.setText(f"{os.path.basename(self.caminho or '')}\n{txt}")

    # ---- copiar e procurar ------------------------------------------------
    def copiar_tudo(self):
        QGuiApplication.clipboard().setText(self.texto.toPlainText())
        self._aviso("✓ Texto inteiro copiado")

    def _copiar_texto_selecionado(self):
        """Ctrl+C no modo "Só texto": tira as marcas de página e põe a fonte, se ligada."""
        cur = self.texto.textCursor()
        sel = cur.selectedText().replace(" ", "\n")
        if not sel:
            return
        antes = self.texto.toPlainText()[:cur.selectionStart()]
        marcas = MARCA.findall(antes)
        pa = int(marcas[-1]) - 1 if marcas else 0
        dentro = MARCA.findall(sel)
        pb = int(dentro[-1]) - 1 if dentro else pa
        limpo = MARCA.sub("", sel).strip()
        while "\n\n\n" in limpo:
            limpo = limpo.replace("\n\n\n", "\n\n")
        QGuiApplication.clipboard().setText(self.visor.com_fonte(limpo, pa, pb) if self.doc is not None else limpo)
        self._aviso(f"✓ {len(limpo):,} caracteres copiados".replace(",", "."))

    def eventFilter(self, obj, ev):
        if obj is self.texto and ev.type() == QEvent.KeyPress and ev.matches(QKeySequence.Copy):
            self._copiar_texto_selecionado()
            return True
        return super().eventFilter(obj, ev)

    def _procurar(self, termo):
        self.t_procura.stop()
        self.procura.setStyleSheet("")
        if self.doc is not None and self.bt_pagina.isChecked():
            self.texto.setExtraSelections([])
            self.visor.procurar(termo)
        else:
            self.visor.procurar("")
            self._destacar(termo)

    def _resultado_visor(self, achou):
        self.procura.setStyleSheet("" if achou or not self.procura.text() else "background:#5a2a2a")

    def tem_texto(self):
        return self.caminho is not None and bool(self.paginas_texto)

    def _destacar(self, termo):
        marcas = []
        if termo:
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#ffe58a"))
            doc = self.texto.document()
            cur = doc.find(termo)
            while not cur.isNull() and len(marcas) < 2000:
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cur
                sel.format = fmt
                marcas.append(sel)
                cur = doc.find(termo, cur)
            if marcas:
                self.texto.setTextCursor(marcas[0].cursor)
                self.texto.centerCursor()
        self.texto.setExtraSelections(marcas)

    def _proximo(self):
        termo = self.procura.text()
        if not termo:
            return
        if self.t_procura.isActive():      # Enter antes da espera acabar: procura já
            self._procurar(termo)
            return
        if self.doc is not None and self.bt_pagina.isChecked():
            self.visor.proximo()
            return
        doc = self.texto.document()
        cur = doc.find(termo, self.texto.textCursor())
        if cur.isNull():
            cur = doc.find(termo)
        if not cur.isNull():
            self.texto.setTextCursor(cur)
            self.texto.centerCursor()
