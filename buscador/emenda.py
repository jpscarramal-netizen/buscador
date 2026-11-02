"""Regra única para juntar pedaços de texto do PDF (linhas e blocos) sem partir frases."""
import re

MARCADOR = re.compile(r"^([•●▪■◦\-–*]|\d{1,3}[.)]|[a-zA-Z][.)])(\s|$)")
ROTULO = re.compile(r"^(\d{1,3}|[a-zA-Z])[.)]$")
FIM_FRASE = re.compile(r"[.!?:;]['\")\]]*$")


def separador(antes, depois, quebra="\n"):
    """O que pôr entre dois pedaços: '' (hífen), ' ' (frase continua) ou a quebra."""
    if not antes or not depois:
        return quebra
    if antes.endswith("-") and depois[:1].islower():
        return ""
    ultima = antes.split()[-1] if antes.split() else antes
    if ROTULO.match(ultima):               # "A)" seguido do texto da alternativa
        return " "
    if MARCADOR.match(depois):
        return quebra
    if depois[:1].islower() or depois[:1] in ",;)]%":
        return " "
    if FIM_FRASE.search(antes):
        return quebra
    if depois[:1].isupper() and len(antes) < 60:   # título ou linha curta
        return quebra
    return " "


def juntar(pedacos, quebra="\n"):
    texto = ""
    for p in pedacos:
        p = " ".join(p.split())
        if not p:
            continue
        sep = separador(texto, p, quebra) if texto else ""
        if sep == "":
            texto = texto[:-1] if texto else texto
        texto += sep + p
    return texto
