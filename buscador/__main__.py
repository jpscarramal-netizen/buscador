"""Ponto de entrada: uma instância só; abrir de novo traz a janela que já está rodando.

Os trabalhos pesados rodam no mesmo executável, num processo à parte:
  --varredura          varre os discos e grava o índice de nomes
  --conteudo LISTA     indexa o texto dos arquivos da lista
"""
import getpass
import os
import sys

NOME = "Buscador-" + getpass.getuser()


def _saida_padrao():
    """No .exe sem console o Python não liga o stdout; religa ao pipe que a janela abriu."""
    if sys.stdout is None:
        import ctypes
        import msvcrt
        h = ctypes.windll.kernel32.GetStdHandle(-11)
        sys.stdout = open(msvcrt.open_osfhandle(h, os.O_WRONLY), "w", encoding="utf-8", buffering=1)
    else:
        sys.stdout.reconfigure(encoding="utf-8")


def _diagnostico():
    """Confere se cada peça carrega (útil para relatar problema): Buscador.exe --diagnostico > diag.txt"""
    from buscador import config
    print("dados:", config.PASTA_DADOS, "| dados grandes:", config.PASTA_GRANDE)
    testes = {
        "pymupdf": lambda: __import__("pymupdf").__doc__ is not None,
        "word (docx)": lambda: __import__("docx").Document() is not None,
        "powerpoint (pptx)": lambda: __import__("pptx").Presentation() is not None,
        "excel (openpyxl)": lambda: __import__("openpyxl").Workbook() is not None,
        "lixeira (send2trash)": lambda: __import__("send2trash") is not None,
        "eventos (watchdog)": lambda: __import__("watchdog.observers").observers.Observer() is not None,
        "busca no conteúdo (fts5)": lambda: __import__("sqlite3").connect(":memory:").execute(
            "create virtual table t using fts5(x)") is not None,
        "ocr do windows": lambda: __import__("buscador.ocr").ocr.disponivel(),
    }
    falhou = 0
    for nome, fn in testes.items():
        try:
            ok = bool(fn())
        except Exception as e:
            ok, nome = False, f"{nome} ({e})"
        falhou += not ok
        print("ok   " if ok else "FALHA", nome)
    return 1 if falhou else 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--varredura":
        _saida_padrao()
        from buscador.varredura import main as varrer
        return varrer()
    if len(sys.argv) > 1 and sys.argv[1] == "--diagnostico":
        _saida_padrao()
        return _diagnostico()
    if len(sys.argv) > 2 and sys.argv[1] == "--conteudo":
        _saida_padrao()
        from buscador.conteudo import indexar
        indexar(sys.argv[2])
        return 0

    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Buscador")
    app.setQuitOnLastWindowClosed(False)

    sock = QLocalSocket()
    sock.connectToServer(NOME)
    if sock.waitForConnected(300):
        sock.write(b"mostrar")
        sock.waitForBytesWritten(300)
        return 0

    from buscador.janela import Janela
    janela = Janela()
    QLocalServer.removeServer(NOME)
    servidor = QLocalServer()
    servidor.listen(NOME)
    servidor.newConnection.connect(lambda: (servidor.nextPendingConnection(), janela.mostrar()))
    if "--oculto" not in sys.argv:
        janela.mostrar()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
