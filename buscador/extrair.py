"""Texto de cada tipo de arquivo, por página (PDF), slide (PowerPoint), planilha (Excel) ou inteiro.

Usado pela prévia e pelo índice do conteúdo, para os dois verem o mesmo texto.
"""
import os

from .emenda import juntar

EXT_TEXTO = {"txt", "md", "csv", "tsv", "log", "json", "xml", "html", "htm", "css", "js", "ts", "tsx", "py", "r",
             "sql", "ini", "cfg", "yaml", "yml", "bat", "ps1", "sh", "tex", "bib", "ris", "srt", "vtt"}
EXT_IMAGEM = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "ico", "tif", "tiff"}
EXT_OFFICE = {"docx", "pptx", "xlsx", "xlsm"}
EXT_DOC = {"pdf", "epub", "xps", "oxps", "mobi", "fb2", "cbz"}   # o PyMuPDF abre todos
LIMITE_TEXTO = 4_000_000
LINHAS_PLANILHA = 2000


def longo(caminho):
    """Caminho que o Windows abre mesmo passando de 260 caracteres (prefixo de caminho longo)."""
    if len(caminho) < 248 or caminho.startswith("\\\\?\\"):
        return caminho
    if caminho.startswith("\\\\"):                       # rede: \\servidor\pasta
        return "\\\\?\\UNC\\" + caminho[2:]
    return "\\\\?\\" + caminho


def ext(caminho):
    return os.path.splitext(caminho)[1].lower().lstrip(".")


def paragrafos(pagina):
    """Texto de uma página do PyMuPDF em parágrafos: junta linhas e blocos quando a frase continua."""
    linhas = []
    for b in pagina.get_text("blocks", sort=True):
        if b[6] == 0:          # 1 = bloco de imagem
            linhas.extend(b[4].splitlines())
    return juntar(linhas, "\n\n")


def texto_puro(caminho):
    with open(longo(caminho), "rb") as f:
        bruto = f.read(LIMITE_TEXTO)
    for cod in ("utf-8", "cp1252"):
        try:
            return bruto.decode(cod)
        except UnicodeDecodeError:
            pass
    return bruto.decode("latin-1")


def _docx(caminho):
    import docx
    d = docx.Document(longo(caminho))
    partes = []
    corpo = d.element.body
    # parágrafos e tabelas na ordem em que aparecem
    for el in corpo.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            t = "".join(n.text or "" for n in el.iter() if n.tag.endswith("}t"))
            partes.append(t)
        elif tag == "tbl":
            for tr in el.iter():
                if tr.tag.endswith("}tr"):
                    cels = []
                    for tc in tr:
                        if tc.tag.endswith("}tc"):
                            cels.append(" ".join("".join(n.text or "" for n in p.iter() if n.tag.endswith("}t"))
                                                 for p in tc.iter() if p.tag.endswith("}p")).strip())
                    partes.append(" | ".join(cels))
            partes.append("")
    texto = "\n".join(partes)
    while "\n\n\n" in texto:
        texto = texto.replace("\n\n\n", "\n\n")
    return [("", texto.strip())]


def _pptx(caminho):
    from pptx import Presentation
    pres = Presentation(longo(caminho))
    saida = []
    for k, slide in enumerate(pres.slides, 1):
        linhas = []
        for forma in slide.shapes:
            if forma.has_text_frame:
                for p in forma.text_frame.paragraphs:
                    t = "".join(r.text for r in p.runs).strip()
                    if t:
                        linhas.append(t)
            elif getattr(forma, "has_table", False) and forma.has_table:
                for linha in forma.table.rows:
                    linhas.append(" | ".join(c.text.strip() for c in linha.cells))
        if slide.has_notes_slide:
            notas = slide.notes_slide.notes_text_frame.text.strip()
            if notas:
                linhas.append("Notas: " + notas)
        saida.append((f"Slide {k}", "\n".join(linhas)))
    return saida


def _xlsx(caminho):
    import openpyxl
    wb = openpyxl.load_workbook(longo(caminho), read_only=True, data_only=True)
    saida = []
    try:
        for ws in wb.worksheets:
            linhas = []
            for k, row in enumerate(ws.iter_rows(values_only=True)):
                if k >= LINHAS_PLANILHA:
                    linhas.append(f"(mostrando as primeiras {LINHAS_PLANILHA} linhas)")
                    break
                cels = ["" if v is None else str(v) for v in row]
                while cels and not cels[-1]:
                    cels.pop()
                if cels:
                    linhas.append("\t".join(cels))
            saida.append((f"Planilha {ws.title}", "\n".join(linhas)))
    finally:
        wb.close()
    return saida


def office(caminho):
    """[(rótulo, texto)] de Word, PowerPoint ou Excel."""
    e = ext(caminho)
    if e == "docx":
        return _docx(caminho)
    if e == "pptx":
        return _pptx(caminho)
    return _xlsx(caminho)
