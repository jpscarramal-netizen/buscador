"""Sinônimos e siglas: "iam" também acha "infarto agudo do miocárdio".

A lista fica num arquivo de texto que dá para editar (Ferramentas > Editar sinônimos):
uma linha por grupo, termos separados por "=".
"""
import os

from .config import PASTA_DADOS
from .indice import chave

ARQUIVO = os.path.join(PASTA_DADOS, "sinonimos.txt")

PADRAO = """# Um grupo por linha, termos separados por "=". Buscar qualquer um acha todos.
# Sem diferença entre maiúscula, minúscula e acento. Linhas com # são comentário.
iam = infarto agudo do miocárdio = infarto
avc = acidente vascular cerebral = derrame
has = hipertensão arterial sistêmica = hipertensão
dm = diabetes mellitus = diabetes
icc = insuficiência cardíaca
dpoc = doença pulmonar obstrutiva crônica
tep = tromboembolismo pulmonar = embolia pulmonar
tvp = trombose venosa profunda
drc = doença renal crônica = insuficiência renal crônica = irc
lra = lesão renal aguda = insuficiência renal aguda
itu = infecção do trato urinário = infecção urinária
dii = doença inflamatória intestinal
drge = doença do refluxo gastroesofágico = refluxo
hda = hemorragia digestiva alta
hdb = hemorragia digestiva baixa
sca = síndrome coronariana aguda
fa = fibrilação atrial
pcr = parada cardiorrespiratória
rcp = reanimação cardiopulmonar
tce = traumatismo cranioencefálico
ist = dst = infecção sexualmente transmissível
les = lúpus
tb = tuberculose
go = ginecologia e obstetrícia = ginecologia = obstetrícia
ped = pediatria
cm = clínica médica
cx = cirurgia
prev = preventiva
uti = terapia intensiva
ecg = eletrocardiograma
rx = radiografia = raio x
tc = tomografia
rm = ressonância
usg = ultrassom = ultrassonografia
"""

_cache = (None, {})


def mapa():
    """{termo: [outros termos do grupo]}, relido quando o arquivo muda."""
    global _cache
    if not os.path.exists(ARQUIVO):
        os.makedirs(PASTA_DADOS, exist_ok=True)
        with open(ARQUIVO, "w", encoding="utf-8") as f:
            f.write(PADRAO)
    try:
        mt = os.path.getmtime(ARQUIVO)
    except OSError:
        return {}
    if _cache[0] == mt:
        return _cache[1]
    m = {}
    with open(ARQUIVO, encoding="utf-8") as f:
        for linha in f:
            linha = linha.split("#", 1)[0]
            grupo = [chave(t.strip()) for t in linha.split("=") if t.strip()]
            for t in grupo:
                # sigla de 2 letras só entra se for digitada (senão casaria com tudo)
                m.setdefault(t, []).extend(o for o in grupo if o != t and len(o) >= 3)
    _cache = (mt, m)
    return m


def expandir(termo):
    return [termo] + mapa().get(termo, [])
