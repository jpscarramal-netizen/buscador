# Empacota o Buscador num .exe (pasta dist\Buscador). Rodar: .venv\Scripts\pyinstaller Buscador.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ocultos = (collect_submodules("winrt") + collect_submodules("watchdog.observers")
           + ["send2trash", "docx", "pptx", "openpyxl"])
dados = collect_data_files("docx") + collect_data_files("pptx")

a = Analysis(
    ["Buscador.pyw"],
    pathex=["."],
    datas=dados,
    hiddenimports=ocultos,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtPdf", "pymupdf4llm", "pymupdf_layout",
              "numpy", "pandas", "matplotlib"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Buscador", icon="buscador.ico",
          console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Buscador", upx=False)
