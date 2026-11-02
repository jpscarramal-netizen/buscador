"""Achar arquivos repetidos: mesmo nome e mesmo tamanho em pastas diferentes."""
import os
import subprocess
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .abrir import para_lixeira
from .consulta import buscar


def _mb(b):
    v, u = (b / 1024 ** 3, "GB") if b >= 1024 ** 3 else (b / 1024 ** 2, "MB")
    return f"{v:,.1f} {u}".replace(",", "X").replace(".", ",").replace("X", ".")


def grupos(idx, exts, min_kb):
    """[(desperdício em bytes, nome, tamanho, [posições])] do maior desperdício para o menor."""
    res = buscar(idx, "arquivo: ext:" + ";".join(exts)) if exts else buscar(idx, "arquivo:")
    por = {}
    minimo = min_kb * 1024
    with idx.trava:
        for i in res:
            t = idx.tam[i]
            if t >= minimo:
                por.setdefault((idx.baixos[i], t), []).append(i)
    saida = [((len(v) - 1) * t, idx.nomes[v[0]], t, v) for (_, t), v in por.items() if len(v) > 1]
    saida.sort(key=lambda g: -g[0])
    return saida


class _Ponte(QObject):
    pronto = Signal(object)


class Duplicados(QDialog):
    def __init__(self, janela):
        super().__init__(janela)
        self.janela = janela
        self.setWindowTitle("Arquivos duplicados")
        self.resize(1100, 700)
        self.ponte = _Ponte()
        self.ponte.pronto.connect(self._mostrar)

        self.exts = QLineEdit("pdf")
        self.exts.setToolTip("Extensões separadas por ponto e vírgula; vazio = todos os arquivos")
        self.min_kb = QSpinBox(minimum=0, maximum=10_000_000, value=100, suffix=" KB")
        bt = QPushButton("Procurar")
        bt.clicked.connect(self.procurar)
        topo = QHBoxLayout()
        for w in (QLabel("Tipos:"), self.exts, QLabel("Tamanho mínimo:"), self.min_kb, bt):
            topo.addWidget(w)
        topo.addStretch(1)

        self.resumo = QLabel("Mesmo nome e mesmo tamanho contam como cópia.")
        self.arvore = QTreeWidget()
        self.arvore.setHeaderLabels(["Arquivo / pasta", "Data", "Tamanho"])
        self.arvore.setColumnWidth(0, 760)
        self.arvore.setColumnWidth(1, 130)
        self.arvore.itemDoubleClicked.connect(lambda it, _: self._abrir_pasta(it))

        b_marcar = QPushButton("Marcar as cópias (deixa a mais antiga)")
        b_marcar.clicked.connect(self.marcar_copias)
        b_limpar = QPushButton("Desmarcar tudo")
        b_limpar.clicked.connect(lambda: self._marcar(lambda k, n: False))
        b_pasta = QPushButton("Abrir a pasta")
        b_pasta.clicked.connect(lambda: self._abrir_pasta(self.arvore.currentItem()))
        self.b_lixeira = QPushButton("Mandar as marcadas para a Lixeira")
        self.b_lixeira.clicked.connect(self.lixeira)
        baixo = QHBoxLayout()
        for w in (b_marcar, b_limpar, b_pasta):
            baixo.addWidget(w)
        baixo.addStretch(1)
        baixo.addWidget(self.b_lixeira)

        lay = QVBoxLayout(self)
        lay.addLayout(topo)
        lay.addWidget(self.resumo)
        lay.addWidget(self.arvore, 1)
        lay.addLayout(baixo)
        self.procurar()

    def procurar(self):
        idx = self.janela.idx
        if idx is None:
            return
        exts = [e.strip(" .").lower() for e in self.exts.text().split(";") if e.strip(" .")]
        self.resumo.setText("Procurando…")
        self.arvore.clear()
        threading.Thread(target=lambda: self.ponte.pronto.emit((idx, grupos(idx, exts, self.min_kb.value()))),
                         daemon=True).start()

    def _mostrar(self, dados):
        idx, gs = dados
        self.arvore.clear()
        total = sum(g[0] for g in gs)
        n = f"{len(gs):,}".replace(",", ".")
        self.resumo.setText(f"{n} arquivos repetidos; apagar as cópias liberaria {_mb(total)}. "
                            "Duplo clique abre a pasta.")
        for desp, nome, tam, pos in gs[:3000]:
            pai = QTreeWidgetItem([f"{nome}   ({len(pos)} cópias, {_mb(desp)} a liberar)", "", _mb(tam)])
            f = pai.font(0)
            f.setBold(True)
            pai.setFont(0, f)
            itens = sorted(((idx.data[i], idx.caminho(i)) for i in pos))
            for data, cam in itens:
                filho = QTreeWidgetItem([cam, datetime.fromtimestamp(data).strftime("%d/%m/%Y %H:%M"), ""])
                filho.setCheckState(0, Qt.Unchecked)
                filho.setData(0, Qt.UserRole, cam)
                pai.addChild(filho)
            self.arvore.addTopLevelItem(pai)
        if gs[:30]:
            for k in range(min(30, self.arvore.topLevelItemCount())):
                self.arvore.topLevelItem(k).setExpanded(True)

    def _marcar(self, regra):
        for g in range(self.arvore.topLevelItemCount()):
            pai = self.arvore.topLevelItem(g)
            for k in range(pai.childCount()):
                pai.child(k).setCheckState(0, Qt.Checked if regra(k, pai.childCount()) else Qt.Unchecked)

    def marcar_copias(self):
        self._marcar(lambda k, n: k > 0)      # filhos já em ordem de data: o primeiro é o mais antigo

    def _marcados(self):
        out = []
        for g in range(self.arvore.topLevelItemCount()):
            pai = self.arvore.topLevelItem(g)
            for k in range(pai.childCount()):
                if pai.child(k).checkState(0) == Qt.Checked:
                    out.append(pai.child(k).data(0, Qt.UserRole))
        return out

    def _abrir_pasta(self, it):
        cam = it.data(0, Qt.UserRole) if it else None
        if cam:
            subprocess.Popen(["explorer", "/select,", cam])

    def lixeira(self):
        sel = self._marcados()
        if not sel:
            QMessageBox.information(self, "Lixeira", "Marque primeiro as cópias que quer tirar.")
            return
        tam = sum(os.path.getsize(c) for c in sel if os.path.exists(c))
        r = QMessageBox.question(self, "Mandar para a Lixeira",
                                 f"Mandar {len(sel)} arquivo(s) ({_mb(tam)}) para a Lixeira?\n"
                                 "Dá para recuperar pela Lixeira do Windows.")
        if r != QMessageBox.Yes:
            return
        erros = para_lixeira(sel)
        if erros:
            QMessageBox.warning(self, "Alguns não foram", "\n".join(erros[:20]))
        self.procurar()
