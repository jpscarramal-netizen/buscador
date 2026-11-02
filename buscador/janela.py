"""Janela principal: caixa de busca, lista de resultados e prévia, como no Everything.

A mais: busca dentro dos arquivos (com página e trecho), favoritos e histórico, recentes no
topo, arrastar arquivos para outros programas, atalho global e duplicados.
"""
import ctypes
import os
import re
import subprocess
import sys
import threading
import time
from array import array
from bisect import bisect_left
from ctypes import wintypes
from datetime import datetime

from PySide6.QtCore import (QAbstractTableModel, QFileInfo, QItemSelectionModel, QMimeData, QModelIndex, QObject,
                            Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import QAction, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileIconProvider, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QSplitter,
                               QSystemTrayIcon, QTableView, QToolButton, QVBoxLayout, QWidget)

from . import abrir as abridor
from . import config, conteudo, sinonimos
from .consulta import _tokens, buscar, separar_conteudo
from .indice import PASTA_DADOS, Indice, unidades_fixas
from .previa import Previa

COLUNAS = ["Nome", "Caminho", "Tamanho", "Data de modificação", "Trecho encontrado"]
COL_TRECHO = 4
MAX_TOPO = 20
ID_ATALHO = 0xB05C
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_SPACE, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x20, 0x0312

AJUDA = """<b>Busca nos nomes (igual ao Everything)</b><br><br>
<code>aula cirurgia</code>: nomes com as duas palavras · <code>aula|prova</code>: uma ou outra · <code>!temp</code>: exclui<br>
<code>*.pdf</code> ou <code>ext:pdf;docx</code>: por extensão · <code>"nome com espaço"</code>: frase exata<br>
<code>pasta:</code> só pastas · <code>arquivo:</code> só arquivos · <code>documents\\ ext:pdf</code>: termo com \\ procura no caminho<br>
Sem diferença de acento: <code>cancer</code> acha "Câncer". Siglas viram sinônimos: <code>iam</code> acha "infarto".<br><br>
<b>Data e tamanho</b><br>
<code>dm:hoje</code> · <code>dm:ontem</code> · <code>dm:semana</code> · <code>dm:mes</code> · <code>dm:ano</code> · <code>dm:15d</code><br>
<code>dm:01/10/2026</code> · <code>dm:&gt;01/10/2026</code> · <code>dm:01/10/2026..15/10/2026</code> · <code>dm:10/2026</code><br>
<code>tamanho:&gt;10mb</code> · <code>tamanho:&lt;500kb</code> · <code>tamanho:1mb..20mb</code> · <code>tamanho:vazio</code><br><br>
<b>Dentro dos arquivos</b><br>
Ligue o botão <b>Conteúdo</b> (Ctrl+D) e digite o assunto: cada resultado mostra a página e o trecho.<br>
Ou, na busca normal: <code>conteudo:apendicite</code> · <code>conteudo:"lesão hepática" ext:pdf</code><br>
Enter abre o PDF já na página do trecho.<br><br>
<b>Atalhos</b><br>
Alt+Espaço (de qualquer lugar): mostrar/esconder o Buscador<br>
Enter: abrir · Ctrl+Enter: abrir a pasta · Ctrl+Shift+C: copiar caminho · Ctrl+B: favoritos e histórico<br>
Ctrl+T: copiar o texto do arquivo · Alt+P: prévia · Ctrl+F: procurar no texto · Shift+arrastar na página: recortar imagem<br>
Arraste um arquivo da lista para o WhatsApp, o e-mail ou uma pasta."""


def _tamanho(b):
    if b < 0:
        return ""
    kb = (b + 1023) // 1024
    return f"{kb:,} KB".replace(",", ".")


def icone_app():
    from .icone import icone_qt
    return icone_qt()


def _trabalho(args, prioridade):
    """Roda --varredura ou --conteudo num processo à parte (no .exe, o próprio executável)."""
    if getattr(sys, "frozen", False):
        cmd, raiz = [sys.executable, *args], None
    else:
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = [sys.executable.replace("pythonw.exe", "python.exe"), "-X", "utf8", "-m", "buscador", *args]
    return subprocess.Popen(cmd, cwd=raiz, stdout=subprocess.PIPE, text=True, encoding="utf-8",
                            creationflags=subprocess.CREATE_NO_WINDOW | prioridade)


def _comando_inicio():
    """Linha que o Windows roda ao entrar, para abrir o Buscador escondido na bandeja."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --oculto'
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f'"{sys.executable.replace("python.exe", "pythonw.exe")}" "{os.path.join(raiz, "Buscador.pyw")}" --oculto'


CHAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
ATALHO_INICIO = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\Buscador.lnk")


def abre_com_windows():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN) as k:
            winreg.QueryValueEx(k, "Buscador")
            return True
    except OSError:
        return os.path.exists(ATALHO_INICIO)


def definir_inicio(ligado):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN, 0, winreg.KEY_SET_VALUE) as k:
        if ligado:
            winreg.SetValueEx(k, "Buscador", 0, winreg.REG_SZ, _comando_inicio())
        else:
            try:
                winreg.DeleteValue(k, "Buscador")
            except OSError:
                pass
    if not ligado and os.path.exists(ATALHO_INICIO):
        os.remove(ATALHO_INICIO)


def _termo_marcado(trecho):
    """Primeira palavra destacada no trecho da busca no conteúdo (entre « »)."""
    m = re.search(r"«([^»]+)»", trecho or "")
    return m.group(1) if m else None


class Modelo(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.idx = None
        self.res = array("i")
        self.extras = {}           # posição -> (página, trecho, páginas com achado)
        self.n_topo = 0            # primeiras linhas = abertos recentemente
        self.icones = QFileIconProvider()
        self.cache_icone = {}
        self.icone_pasta = self.icones.icon(QFileIconProvider.IconType.Folder)
        self.icone_disco = self.icones.icon(QFileIconProvider.IconType.Drive)

    def trocar(self, idx, res, extras=None, n_topo=0):
        self.beginResetModel()
        self.idx, self.res, self.extras, self.n_topo = idx, res, extras or {}, n_topo
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.res)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUNAS)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUNAS[s]
        return None

    def item(self, linha):
        return self.res[linha]

    def flags(self, m):
        return super().flags(m) | Qt.ItemIsDragEnabled

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indices):
        """Arrastar para fora: o Windows recebe os arquivos (WhatsApp, e-mail, Explorer)."""
        linhas = sorted({m.row() for m in indices})
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(self.idx.caminho(self.res[r])) for r in linhas[:200]])
        return md

    def _icone(self, i):
        idx = self.idx
        if idx.pais[i] < 0:
            return self.icone_disco
        if idx.pasta[i]:
            return self.icone_pasta
        ext = os.path.splitext(idx.baixos[i])[1]
        chave = idx.caminho(i) if ext in (".exe", ".lnk", ".ico", ".url") else ext
        ic = self.cache_icone.get(chave)
        if ic is None:
            ic = self.icones.icon(QFileInfo(idx.caminho(i)))
            if len(self.cache_icone) < 5000:
                self.cache_icone[chave] = ic
        return ic

    def data(self, m, role=Qt.DisplayRole):
        if not m.isValid() or m.row() >= len(self.res):
            return None
        i = self.res[m.row()]
        idx, c = self.idx, m.column()
        if role == Qt.DisplayRole:
            if c == 0:
                return idx.nomes[i]
            if c == 1:
                return idx.pasta_de(i)
            if c == 2:
                return _tamanho(idx.tam[i])
            if c == 3:
                d = idx.data[i]
                return datetime.fromtimestamp(d).strftime("%d/%m/%Y %H:%M") if d > 0 else ""
            if c == COL_TRECHO and i in self.extras:
                pag, trecho, n = self.extras[i]
                return f"p. {pag}: {trecho.replace('«', '').replace('»', '')}"
        elif role == Qt.DecorationRole and c == 0:
            return self._icone(i)
        elif role == Qt.TextAlignmentRole and c == 2:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        elif role == Qt.ToolTipRole:
            if c == COL_TRECHO and i in self.extras:
                pag, trecho, n = self.extras[i]
                mais = f" (o termo aparece em {n} páginas)" if n > 1 else ""
                return f"Página {pag}{mais}\n\n{trecho.replace('«', '').replace('»', '')}"
            if c in (0, 1):
                return idx.caminho(i) + ("\n(aberto recentemente)" if m.row() < self.n_topo else "")
        elif role == Qt.FontRole and m.row() < self.n_topo and c == 0:
            f = QFont()
            f.setBold(True)
            return f
        return None


class Ponte(QObject):
    resultado = Signal(int, object, object, float, object, int)  # pedido, índice, resultado, ms, extras, n_topo
    progresso = Signal(str)
    progresso_conteudo = Signal(str)
    indice_pronto = Signal(object, bool)             # índice, veio do disco
    mudou = Signal()
    recentes = Signal(object)


class Observador:
    """Eventos do Windows (ReadDirectoryChangesW) via watchdog, aplicados no índice atual."""

    def __init__(self, janela):
        self.janela = janela
        self.trava = threading.Lock()
        self.fila_rescan = None
        self.obs = None

    def iniciar(self, raizes):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        eu = self
        ignorar = (PASTA_DADOS.lower(), config.PASTA_GRANDE.lower())

        class H(FileSystemEventHandler):
            def on_any_event(self, ev):
                if ev.event_type not in ("created", "deleted", "modified", "moved"):
                    return
                if ev.src_path.lower().startswith(ignorar):
                    return
                eu.aplicar((ev.event_type, ev.src_path, getattr(ev, "dest_path", "")))

        self.obs = Observer()
        for r in raizes:
            try:
                self.obs.schedule(H(), r, recursive=True)
            except OSError:
                pass
        self.obs.daemon = True
        self.obs.start()

    def aplicar(self, ev):
        with self.trava:
            alvo = self.janela.idx
            if alvo is None:
                return
            self._aplicar_em(alvo, ev)
            if self.fila_rescan is not None:
                self.fila_rescan.append(ev)
        self.janela.ponte.mudou.emit()

    def trocar_indice(self, novo):
        """Reaplica no índice novo o que mudou durante a varredura e troca."""
        with self.trava:
            for ev in self.fila_rescan or []:
                self._aplicar_em(novo, ev)
            self.fila_rescan = None
            self.janela.idx = novo

    @staticmethod
    def _aplicar_em(idx, ev):
        tipo, src, dst = ev
        try:
            if tipo == "created":
                idx.criado(src)
            elif tipo == "deleted":
                idx.removido(src)
            elif tipo == "modified":
                idx.alterado(src)
            elif tipo == "moved":
                idx.removido(src)
                idx.criado(dst)
        except Exception:
            pass


def _por_cima_recentes(idx, res, recentes):
    """Põe no topo os itens abertos recentemente (res em ordem de nome)."""
    chave = idx.baixos.__getitem__
    pos = []
    for i in recentes:
        if i >= len(idx.baixos) or idx.apagado[i]:
            continue
        b = idx.baixos[i]
        p = bisect_left(res, b, key=chave)
        while p < len(res) and idx.baixos[res[p]] == b:
            if res[p] == i:
                pos.append(p)
                break
            p += 1
        if len(pos) >= MAX_TOPO:
            break
    if not pos:
        return res, 0
    topo = array("i", (res[p] for p in pos))
    resto = array("i", res)
    for p in sorted(pos, reverse=True):
        del resto[p]
    return topo + resto, len(topo)


class Janela(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Buscador")
        self.setWindowIcon(icone_app())
        self.resize(1400, 820)
        self.idx = None
        self.pedido = 0
        self.ordem = (0, Qt.AscendingOrder)
        self.saindo = False
        self.recentes_idx = []
        self.proc_conteudo = None
        self.trava_conteudo = threading.Lock()
        self.atalho = None
        self.ponte = Ponte()
        self.ponte.resultado.connect(self._resultado)
        self.ponte.progresso.connect(self._progresso)
        self.ponte.progresso_conteudo.connect(lambda t: self.st_conteudo.setText(t))
        self.ponte.indice_pronto.connect(self._indice_pronto)
        self.ponte.mudou.connect(lambda: self.t_mudou.isActive() or self.t_mudou.start())
        self.ponte.recentes.connect(self._chegaram_recentes)
        self.obs = Observador(self)

        self.busca = QLineEdit()
        self.busca.setClearButtonEnabled(True)
        self.busca.setPlaceholderText("Buscar arquivos e pastas…   (F1: sintaxe)")
        f = self.busca.font()
        f.setPointSize(11)
        self.busca.setFont(f)
        self.busca.textChanged.connect(lambda: (self.t_busca.start(), self.t_hist.start()))
        self.busca.returnPressed.connect(self.abrir)
        self.busca.installEventFilter(self)

        self.bt_conteudo = QToolButton(text="Conteúdo", checkable=True)
        self.bt_conteudo.setToolTip("Buscar dentro dos PDFs e documentos (Ctrl+D)")
        self.bt_conteudo.toggled.connect(self._modo_conteudo)
        self.bt_fav = QToolButton(text="★")
        self.bt_fav.setToolTip("Favoritos e histórico (Ctrl+B)")
        self.bt_fav.setPopupMode(QToolButton.InstantPopup)
        self.menu_fav = QMenu(self)
        self.menu_fav.aboutToShow.connect(self._montar_favoritos)
        self.bt_fav.setMenu(self.menu_fav)
        for b in (self.bt_conteudo, self.bt_fav):
            b.setFont(f)
            b.setMinimumHeight(self.busca.sizeHint().height())

        self.modelo = Modelo()
        self.tabela = QTableView()
        self.tabela.setModel(self.modelo)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tabela.setShowGrid(False)
        self.tabela.setWordWrap(False)
        self.tabela.setDragEnabled(True)
        self.tabela.setDragDropMode(QAbstractItemView.DragOnly)
        self.tabela.setDefaultDropAction(Qt.CopyAction)
        self.tabela.verticalHeader().hide()
        self.tabela.verticalHeader().setDefaultSectionSize(22)
        self.tabela.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        h = self.tabela.horizontalHeader()
        h.setSectionsClickable(True)
        h.setSortIndicatorShown(True)
        h.setSortIndicator(0, Qt.AscendingOrder)
        h.sortIndicatorChanged.connect(self._ordenar)
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for c, w in enumerate((360, 400, 90, 125, 600)):
            self.tabela.setColumnWidth(c, w)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setStretchLastSection(False)
        h.moveSection(COL_TRECHO, 1)          # o trecho aparece logo depois do nome
        self.tabela.setColumnHidden(COL_TRECHO, True)
        self.tabela.doubleClicked.connect(lambda _: self.abrir())
        self.tabela.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabela.customContextMenuRequested.connect(self._menu_contexto)
        self.tabela.selectionModel().currentRowChanged.connect(lambda *_: self.t_previa.start())
        self.tabela.installEventFilter(self)

        self.previa = Previa()
        self.previa.ocrFeito.connect(self._ocr_feito)
        self.divisor = QSplitter()
        self.divisor.addWidget(self.tabela)
        self.divisor.addWidget(self.previa)
        self.divisor.setSizes([700, 700])
        self.divisor.setStretchFactor(0, 1)
        self.divisor.setStretchFactor(1, 1)

        linha = QHBoxLayout()
        linha.setSpacing(4)
        linha.addWidget(self.busca, 1)
        linha.addWidget(self.bt_conteudo)
        linha.addWidget(self.bt_fav)
        centro = QWidget()
        lay = QVBoxLayout(centro)
        lay.setContentsMargins(4, 4, 4, 0)
        lay.setSpacing(4)
        lay.addLayout(linha)
        lay.addWidget(self.divisor, 1)
        self.setCentralWidget(centro)

        self.st_total = QLabel()
        self.st_conteudo = QLabel()
        self.st_indice = QLabel()
        self.statusBar().addWidget(self.st_total, 1)
        self.statusBar().addPermanentWidget(self.st_conteudo)
        self.statusBar().addPermanentWidget(self.st_indice)

        self.t_busca = QTimer(singleShot=True, interval=60, timeout=self.pesquisar)
        self.t_previa = QTimer(singleShot=True, interval=120, timeout=self._mostrar_previa)
        self.t_mudou = QTimer(singleShot=True, interval=1500, timeout=self._mudou)
        self.t_hist = QTimer(singleShot=True, interval=2500, timeout=self._guardar_historico)
        self.t_recentes = QTimer(interval=120_000, timeout=self._atualizar_recentes)
        self.buscando = False

        self._menus()
        self._bandeja()
        self._registrar_atalho()
        threading.Thread(target=self._carregar, daemon=True).start()

    # ---- menus e bandeja --------------------------------------------------
    def _acao(self, menu, nome, fn, atalho=None, marcavel=None):
        a = QAction(nome, self)
        a.triggered.connect(fn)
        if atalho:
            a.setShortcut(QKeySequence(atalho))
            a.setShortcutContext(Qt.WindowShortcut)
        if marcavel is not None:
            a.setCheckable(True)
            a.setChecked(marcavel)
        menu.addAction(a)
        return a

    def _menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&Arquivo")
        self._acao(m, "Abrir", self.abrir)
        self._acao(m, "Abrir a pasta do arquivo", self.abrir_pasta, "Ctrl+Return")
        m.addSeparator()
        self._acao(m, "Fechar janela (continua na bandeja)", self.hide, "Esc")
        self._acao(m, "Sair", self.sair, "Ctrl+Q")
        m = mb.addMenu("&Editar")
        self._acao(m, "Copiar caminho completo", self.copiar_caminho, "Ctrl+Shift+C")
        self._acao(m, "Copiar nome", self.copiar_nome)
        self._acao(m, "Copiar texto do arquivo", self.copiar_texto, "Ctrl+T")
        m = mb.addMenu("&Visualização")
        self.a_previa = self._acao(m, "Pré-visualização", self._alternar_previa, "Alt+P", True)
        self._acao(m, "Abertos recentemente no topo", self._alternar_recentes, None, config.get("recentes_no_topo"))
        m.addSeparator()
        self._acao(m, "Abrir junto com o Windows", definir_inicio, None, abre_com_windows())
        m = mb.addMenu("&Pesquisar")
        self._acao(m, "Buscar dentro dos arquivos", lambda: self.bt_conteudo.toggle(), "Ctrl+D")
        self._acao(m, "Favoritos e histórico", lambda: self.bt_fav.showMenu(), "Ctrl+B")
        self._acao(m, "Adicionar esta busca aos favoritos…", self.adicionar_favorito)
        m.addSeparator()
        self._acao(m, "Só PDFs", lambda: self._filtro("ext:pdf"))
        self._acao(m, "Só documentos", lambda: self._filtro("ext:pdf;doc;docx;odt;rtf;txt;pptx;ppt;xlsx;xls;csv"))
        self._acao(m, "Só pastas", lambda: self._filtro("pasta:"))
        self._acao(m, "Modificados hoje", lambda: self._filtro("dm:hoje"))
        self._acao(m, "Modificados nesta semana", lambda: self._filtro("dm:semana"))
        self._acao(m, "Maiores que 100 MB", lambda: self._filtro("tamanho:>100mb"))
        self._acao(m, "Limpar filtro", lambda: self._filtro(None))
        m = mb.addMenu("&Ferramentas")
        self._acao(m, "Achar arquivos duplicados…", self.duplicados)
        m.addSeparator()
        self.a_indexar = self._acao(m, "Indexar o conteúdo dos arquivos", self._alternar_conteudo, None,
                                    config.get("indexar_conteudo"))
        self._acao(m, "Pastas que a busca no conteúdo ignora…", self._pastas_ignoradas)
        self._acao(m, "Situação do índice do conteúdo", self._situacao_conteudo)
        self._acao(m, "Editar sinônimos e siglas…", lambda: (sinonimos.mapa(), os.startfile(sinonimos.ARQUIVO)))
        m.addSeparator()
        self._acao(m, "Reindexar nomes agora", self.reindexar)
        m = mb.addMenu("Aj&uda")
        self._acao(m, "Sintaxe de busca e atalhos", lambda: QMessageBox.information(self, "Buscador", AJUDA), "F1")

    def _bandeja(self):
        self.bandeja = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        menu.addAction("Abrir o Buscador", self.mostrar)
        menu.addAction("Reindexar agora", self.reindexar)
        menu.addSeparator()
        menu.addAction("Sair", self.sair)
        self.bandeja.setContextMenu(menu)
        self.bandeja.setToolTip("Buscador")
        self.bandeja.activated.connect(lambda r: self.mostrar() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.bandeja.show()

    def mostrar(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.busca.setFocus()
        self.busca.selectAll()

    def closeEvent(self, e):
        if self.saindo:
            e.accept()
            return
        e.ignore()
        self.hide()

    def sair(self):
        self.saindo = True
        self._parar_conteudo()
        if self.atalho:
            ctypes.windll.user32.UnregisterHotKey(int(self.winId()), ID_ATALHO)
        self.bandeja.hide()
        QApplication.quit()

    # ---- atalho global (Alt+Espaço) ---------------------------------------
    def _registrar_atalho(self):
        u = ctypes.windll.user32
        hwnd = int(self.winId())
        for mods, nome in ((MOD_ALT, "Alt+Espaço"), (MOD_CONTROL | MOD_ALT, "Ctrl+Alt+Espaço")):
            if u.RegisterHotKey(hwnd, ID_ATALHO, mods | MOD_NOREPEAT, VK_SPACE):
                self.atalho = nome
                self.busca.setToolTip(f"{nome} abre o Buscador de qualquer lugar")
                return

    def nativeEvent(self, tipo, msg):
        if tipo == b"windows_generic_MSG":
            m = wintypes.MSG.from_address(int(msg))
            if m.message == WM_HOTKEY and m.wParam == ID_ATALHO:
                if self.isVisible() and self.isActiveWindow():
                    self.hide()
                else:
                    self.mostrar()
                return True, 0
        return super().nativeEvent(tipo, msg)

    # ---- índice -----------------------------------------------------------
    def _carregar(self):
        idx = Indice()
        if idx.carregar():
            self.ponte.indice_pronto.emit(idx, True)
        else:
            self._varrer(primeira=True)

    def _varrer(self, primeira=False):
        if not primeira:
            self.obs.fila_rescan = []
        proc = _trabalho(["--varredura"], subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        segundos = "?"
        for linha in proc.stdout:
            partes = linha.rstrip("\n").split("\t")
            if partes[0] == "P":
                self.ponte.progresso.emit(f"Indexando {partes[1]}  {int(partes[2]):,} itens".replace(",", "."))
            elif partes[0] == "M":
                self.ponte.progresso.emit("Organizando o índice…")
            elif partes[0] == "F":
                segundos = partes[2]
        proc.wait()
        novo = Indice()
        if proc.returncode != 0 or not novo.carregar():
            self.obs.fila_rescan = None
            self.ponte.progresso.emit("A indexação falhou; use Ferramentas > Reindexar")
            return
        if not primeira:
            self.obs.trocar_indice(novo)
        self.ponte.indice_pronto.emit(novo, False)
        self.ponte.progresso.emit(f"Índice atualizado ({segundos} s)")
        self._indexar_conteudo(novo)

    def _indice_pronto(self, idx, do_disco):
        primeira_vez = self.idx is None
        self.idx = idx
        self.pesquisar(manter=not primeira_vez)
        self._atualizar_recentes()
        if primeira_vez:
            self.t_recentes.start()
            self.obs.iniciar(unidades_fixas())
            if do_disco:
                quando = datetime.fromtimestamp(idx.gerado_em).strftime("%d/%m %H:%M")
                self.st_indice.setText(f"Índice de {quando}; conferindo mudanças em segundo plano…")
                threading.Thread(target=self._varrer, daemon=True).start()

    def reindexar(self):
        if self.idx is None or self.obs.fila_rescan is not None:
            return
        threading.Thread(target=self._varrer, daemon=True).start()

    def _progresso(self, txt):
        self.st_indice.setText(txt)

    # ---- índice do conteúdo -----------------------------------------------
    def _candidatos(self, idx):
        exts = config.get("conteudo_ext")
        ignorar = [p.lower() for p in config.get("conteudo_ignorar") if p.strip()]
        prefixos = tuple(p for p in ignorar if re.match(r"^[a-z]:\\", p))
        trechos = [p for p in ignorar if p not in prefixos]
        out = []
        for i in buscar(idx, "arquivo: ext:" + ";".join(exts)):
            c = idx.caminho(i)
            low = c.lower()
            if low.startswith(prefixos) or any(t in low for t in trechos):
                continue
            out.append(c)
        return out

    def _indexar_conteudo(self, idx=None):
        """Roda o indexador do conteúdo (processo à parte, prioridade ociosa); continua de onde parou."""
        if not config.get("indexar_conteudo") or not self.trava_conteudo.acquire(blocking=False):
            return
        try:
            self._rodar_indexador(idx)
        finally:
            self.trava_conteudo.release()

    def _rodar_indexador(self, idx):
        idx = idx or self.idx
        if idx is None:
            return
        cand = self._candidatos(idx)
        os.makedirs(os.path.dirname(conteudo.LISTA), exist_ok=True)
        with open(conteudo.LISTA, "w", encoding="utf-8") as f:
            f.write("\n".join(cand))
        proc = _trabalho(["--conteudo", conteudo.LISTA], 0x40)   # IDLE_PRIORITY_CLASS
        self.proc_conteudo = proc
        inicio = time.time()
        for linha in proc.stdout:
            p = linha.rstrip("\n").split("\t")
            if p[0] == "T" and p[1] != "0":
                self.ponte.progresso_conteudo.emit(f"Conteúdo: {int(p[1]):,} arquivos na fila".replace(",", "."))
            elif p[0] == "P":
                feitos, total = int(p[1]), int(p[2])
                resta = (time.time() - inicio) / feitos * (total - feitos) / 60
                self.ponte.progresso_conteudo.emit(
                    f"Conteúdo: {feitos:,} de {total:,} (faltam ~{resta:.0f} min)".replace(",", "."))
        proc.wait()
        if not self.saindo:
            self.ponte.progresso_conteudo.emit("" if proc.returncode == 0 else "Conteúdo: indexação parada")

    def _parar_conteudo(self):
        if self.proc_conteudo and self.proc_conteudo.poll() is None:
            self.proc_conteudo.kill()

    def _alternar_conteudo(self):
        ligado = self.a_indexar.isChecked()
        config.set("indexar_conteudo", ligado)
        if ligado:
            threading.Thread(target=self._indexar_conteudo, daemon=True).start()
        else:
            self._parar_conteudo()
            self.st_conteudo.setText("Conteúdo: pausado")

    def _pastas_ignoradas(self):
        atual = "\n".join(config.get("conteudo_ignorar"))
        txt, ok = QInputDialog.getMultiLineText(
            self, "Busca no conteúdo",
            "Caminhos que a busca no conteúdo não lê (um por linha).\n"
            "Começando com a letra do disco = a pasta inteira; senão, qualquer caminho que contenha o trecho.",
            atual)
        if ok:
            config.set("conteudo_ignorar", [l.strip() for l in txt.splitlines() if l.strip()])
            self._parar_conteudo()
            threading.Thread(target=self._indexar_conteudo, daemon=True).start()

    def _situacao_conteudo(self):
        ok, dup, vazio, mb = conteudo.resumo()
        QMessageBox.information(
            self, "Índice do conteúdo",
            f"Arquivos com texto no índice: {ok:,}\nCópias puladas (mesmo nome e tamanho): {dup:,}\n"
            f"Sem texto (escaneados ou vazios): {vazio:,}\nTamanho: {mb:,.0f} MB em {conteudo.BANCO}\n\n"
            f"{self.st_conteudo.text() or 'Indexação em dia.'}".replace(",", "."))

    def _ocr_feito(self, caminho, _):
        if not caminho.lower().endswith(".pdf") or not conteudo.existe():
            return

        def gravar():
            con = conteudo.conectar()
            try:
                conteudo.gravar_paginas(con, caminho, conteudo._paginas(caminho))
                con.commit()
            finally:
                con.close()

        threading.Thread(target=gravar, daemon=True).start()

    # ---- recentes ---------------------------------------------------------
    def _atualizar_recentes(self):
        idx = self.idx
        if idx is None or not config.get("recentes_no_topo"):
            return

        def tarefa():
            caminhos = abridor.recentes()[:60]
            out = []
            with idx.trava:
                for c in caminhos:
                    i, _ = idx.localizar(c)
                    if i is not None:
                        out.append(i)
            self.ponte.recentes.emit((idx, out))

        threading.Thread(target=tarefa, daemon=True).start()

    def _chegaram_recentes(self, dados):
        idx, lista = dados
        if idx is self.idx and lista != self.recentes_idx:
            self.recentes_idx = lista
            if not self.buscando:
                self.pesquisar(manter=True)

    def _alternar_recentes(self, ligado):
        config.set("recentes_no_topo", ligado)
        self.recentes_idx = []
        self._atualizar_recentes()
        self.pesquisar(manter=True)

    # ---- favoritos e histórico --------------------------------------------
    def _guardar_historico(self):
        t = self.busca.text().strip()
        if t and self.modelo.rowCount():
            config.empilhar("historico", [t, self.bt_conteudo.isChecked()], 40)

    def _usar_busca(self, texto, no_conteudo):
        self.bt_conteudo.setChecked(bool(no_conteudo))
        self.busca.setText(texto)
        self.busca.setFocus()

    def _montar_favoritos(self):
        m = self.menu_fav
        m.clear()
        m.addAction("Adicionar esta busca aos favoritos…", self.adicionar_favorito)
        favs = config.get("favoritos")
        if favs:
            m.addSeparator()
            for f in favs:
                rotulo = f["nome"] + ("   (conteúdo)" if f.get("conteudo") else "")
                m.addAction("★ " + rotulo, lambda f=f: self._usar_busca(f["busca"], f.get("conteudo")))
            rem = m.addMenu("Remover favorito")
            for k, f in enumerate(favs):
                rem.addAction(f["nome"], lambda k=k: config.set("favoritos", [x for j, x in enumerate(favs) if j != k]))
        hist = config.get("historico")
        m.addSeparator()
        h = m.addMenu("Histórico")
        h.setEnabled(bool(hist))
        for t, cont in hist:
            h.addAction(t + ("   (conteúdo)" if cont else ""), lambda t=t, c=cont: self._usar_busca(t, c))
        if hist:
            h.addSeparator()
            h.addAction("Limpar histórico", lambda: config.set("historico", []))

    def adicionar_favorito(self):
        t = self.busca.text().strip()
        if not t:
            QMessageBox.information(self, "Favoritos", "Digite uma busca primeiro.")
            return
        nome, ok = QInputDialog.getText(self, "Adicionar aos favoritos", "Nome do favorito:", text=t)
        if ok and nome.strip():
            favs = [f for f in config.get("favoritos") if f["nome"] != nome.strip()]
            favs.append({"nome": nome.strip(), "busca": t, "conteudo": self.bt_conteudo.isChecked()})
            config.set("favoritos", favs)
            self.statusBar().showMessage(f"Favorito \"{nome.strip()}\" salvo", 2500)

    # ---- busca ------------------------------------------------------------
    def _filtro(self, f):
        prefs = ("ext:", "pasta:", "folder:", "dm:", "tamanho:", "size:")
        t = " ".join(p for p in self.busca.text().split() if not p.lower().startswith(prefs))
        self.busca.setText((t + " " + f).strip() if f else t)

    def _modo_conteudo(self, ligado):
        self.tabela.setColumnHidden(COL_TRECHO, not ligado)
        self.busca.setPlaceholderText("Buscar dentro dos PDFs e documentos…   (ext:, dm: e pasta\\ também valem)"
                                      if ligado else "Buscar arquivos e pastas…   (F1: sintaxe)")
        h = self.tabela.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.Interactive if ligado else QHeaderView.Stretch)
        if ligado:
            self.tabela.setColumnWidth(1, 260)
        self.pesquisar()
        self.busca.setFocus()

    def _partes(self, consulta):
        """(termos do conteúdo ou None, consulta dos nomes)."""
        cont, resto = separar_conteudo(consulta)
        if not self.bt_conteudo.isChecked():
            return (cont or None), resto
        # modo conteúdo: palavras soltas vão para o texto; filtros (ext:, dm:, caminho\) para os nomes
        nomes = []
        for t in _tokens(resto):
            if re.match(r"^!?\w+:", t) or "\\" in t or "*" in t or "?" in t or t.startswith("!"):
                nomes.append(t)
            else:
                cont.append(t.replace('"', ""))
        return cont, " ".join(nomes)

    def pesquisar(self, manter=False):
        if self.idx is None:
            self.st_total.setText("Carregando o índice…")
            return
        self.pedido += 1
        pedido, idx, consulta, ordem = self.pedido, self.idx, self.busca.text(), self.ordem
        termos, resto = self._partes(consulta)
        topo_ok = config.get("recentes_no_topo") and termos is None and ordem == (0, Qt.AscendingOrder)
        recentes = list(self.recentes_idx) if topo_ok else []
        self._manter = manter
        self.buscando = True
        if not manter:
            QTimer.singleShot(250, lambda: self.buscando and pedido == self.pedido and self.st_total.setText("Buscando…"))

        def tarefa():
            t = time.perf_counter()
            extras = {}
            n_topo = 0
            if termos is not None:
                if not termos:
                    self.ponte.resultado.emit(pedido, idx, array("i"), 0.0, {}, 0)
                    return
                achados = conteudo.procurar(termos)
                ordem_rel = []
                with idx.trava:
                    for cam, pag, trecho, n in achados:
                        i, _ = idx.localizar(cam)
                        if i is not None and i not in extras:
                            extras[i] = (pag, trecho, n)
                            ordem_rel.append(i)
                res = buscar(idx, resto, cancelado=lambda: pedido != self.pedido, limitar=ordem_rel)
                if ordem != (0, Qt.AscendingOrder):
                    res = self._aplicar_ordem(idx, res, ordem)
            else:
                res = buscar(idx, resto, cancelado=lambda: pedido != self.pedido)
                if res is None or pedido != self.pedido:
                    return
                res = self._aplicar_ordem(idx, res, ordem)
                if recentes:
                    res, n_topo = _por_cima_recentes(idx, res, recentes)
            if pedido == self.pedido:
                self.ponte.resultado.emit(pedido, idx, res, (time.perf_counter() - t) * 1000, extras, n_topo)

        threading.Thread(target=tarefa, daemon=True).start()

    @staticmethod
    def _aplicar_ordem(idx, res, ordem):
        col, sentido = ordem
        desc = sentido == Qt.DescendingOrder
        if col == 0:
            return array("i", reversed(res)) if desc else res
        if col == COL_TRECHO:
            return res
        chave = {1: lambda i: idx.pasta_de(i).lower(), 2: idx.tam.__getitem__, 3: idx.data.__getitem__}[col]
        return array("i", sorted(res, key=chave, reverse=desc))

    def _resultado(self, pedido, idx, res, ms, extras, n_topo):
        if pedido != self.pedido:
            return
        self.buscando = False
        atual = None
        if self._manter:
            m = self.tabela.currentIndex()
            if m.isValid() and self.modelo.idx is idx:
                atual = self.modelo.item(m.row())
        topo = self.tabela.verticalScrollBar().value()
        self.modelo.trocar(idx, res, extras, n_topo)
        if self._manter:
            self.tabela.verticalScrollBar().setValue(topo)
            if atual is not None:
                try:
                    linha = res.index(atual)
                    self.tabela.selectionModel().setCurrentIndex(
                        self.modelo.index(linha, 0), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                except ValueError:
                    pass
        else:
            self.tabela.scrollToTop()
        n = len(res)
        txt = f"{n:,} objeto{'s' if n != 1 else ''}".replace(",", ".") + f"   ({ms:.0f} ms)"
        if self.bt_conteudo.isChecked() or extras:
            if not conteudo.existe():
                txt = "O índice do conteúdo ainda está sendo criado (veja o canto da barra)."
            elif not self.busca.text().strip():
                txt = "Digite o assunto para buscar dentro dos arquivos."
            else:
                txt = f"{n:,} arquivo{'s' if n != 1 else ''} com o termo no texto".replace(",", ".") + f"   ({ms:.0f} ms)"
        self.st_total.setText(txt)

    def _mudou(self):
        if self.buscando:
            self.t_mudou.start()
        elif not self.bt_conteudo.isChecked():
            self.pesquisar(manter=True)

    def _ordenar(self, col, sentido):
        self.ordem = (col, sentido)
        self.pesquisar()

    # ---- ações sobre o item ----------------------------------------------
    def _linhas(self):
        if self.modelo.idx is None:
            return []
        linhas = sorted({m.row() for m in self.tabela.selectionModel().selectedRows()})
        if not linhas and self.tabela.currentIndex().isValid():
            linhas = [self.tabela.currentIndex().row()]
        return linhas

    def _selecionados(self):
        return [self.modelo.idx.caminho(self.modelo.item(r)) for r in self._linhas()]

    def abrir(self):
        linhas = self._linhas()
        if not linhas and self.modelo.rowCount():
            linhas = [0]
        for r in linhas[:10]:
            i = self.modelo.item(r)
            c = self.modelo.idx.caminho(i)
            pag = self.modelo.extras.get(i, (None,))[0]
            try:
                abridor.abrir(c, pag)
            except OSError as e:
                QMessageBox.warning(self, "Não abriu", f"{c}\n\n{e.strerror}")
        if linhas:
            self._guardar_historico()
            QTimer.singleShot(1500, self._atualizar_recentes)

    def abrir_pasta(self):
        for c in self._selecionados()[:5]:
            subprocess.Popen(["explorer", "/select,", c])

    def copiar_caminho(self):
        sel = self._selecionados()
        if sel:
            QGuiApplication.clipboard().setText("\r\n".join(sel))
            self.statusBar().showMessage("Caminho copiado", 2000)

    def copiar_nome(self):
        sel = self._selecionados()
        if sel:
            QGuiApplication.clipboard().setText("\r\n".join(os.path.basename(c.rstrip("\\")) for c in sel))
            self.statusBar().showMessage("Nome copiado", 2000)

    def copiar_texto(self):
        if self.previa.tem_texto():
            self.previa.copiar_tudo()
            self.statusBar().showMessage("Texto do arquivo copiado", 2500)
        else:
            self.statusBar().showMessage("Este item não tem texto para copiar", 2500)

    def duplicados(self):
        if self.idx is None:
            return
        from .duplicados import Duplicados
        Duplicados(self).show()

    def _alternar_previa(self):
        self.previa.setVisible(self.a_previa.isChecked())
        if self.previa.isVisible():
            self._mostrar_previa()

    def _mostrar_previa(self):
        if not self.previa.isVisible():
            return
        m = self.tabela.currentIndex()
        if not m.isValid() or self.modelo.idx is None:
            self.previa.limpar()
            return
        i = self.modelo.item(m.row())
        ex = self.modelo.extras.get(i)
        if ex:
            self.previa.mostrar(self.modelo.idx.caminho(i), ex[0], _termo_marcado(ex[1]))
        else:
            self.previa.mostrar(self.modelo.idx.caminho(i))

    def _menu_contexto(self, pos):
        m = self.tabela.indexAt(pos)
        if not m.isValid():
            return
        i = self.modelo.item(m.row())
        menu = QMenu(self)
        ex = self.modelo.extras.get(i)
        menu.addAction(f"Abrir na página {ex[0]}" if ex else "Abrir", self.abrir)
        if ex:
            menu.addAction("Abrir do começo", lambda: abridor.abrir(self.modelo.idx.caminho(i)))
        menu.addAction("Abrir a pasta do arquivo", self.abrir_pasta)
        menu.addSeparator()
        menu.addAction("Copiar caminho completo", self.copiar_caminho)
        menu.addAction("Copiar nome", self.copiar_nome)
        a = menu.addAction("Copiar texto do arquivo", self.copiar_texto)
        a.setEnabled(self.previa.tem_texto())
        menu.addSeparator()
        menu.addAction("Adicionar esta busca aos favoritos…", self.adicionar_favorito)
        menu.exec(self.tabela.viewport().mapToGlobal(pos))

    # ---- teclado: setas da busca descem para a lista ----------------------
    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.KeyPress:
            k = ev.key()
            if obj is self.busca and k in (Qt.Key_Down, Qt.Key_PageDown) and self.modelo.rowCount():
                self.tabela.setFocus()
                if not self.tabela.currentIndex().isValid():
                    self.tabela.selectRow(0)
                return True
            if obj is self.tabela and k in (Qt.Key_Return, Qt.Key_Enter) and not ev.modifiers():
                self.abrir()
                return True
            if obj is self.tabela and k == Qt.Key_Up and self.tabela.currentIndex().row() <= 0:
                self.busca.setFocus()
                return True
        return super().eventFilter(obj, ev)
