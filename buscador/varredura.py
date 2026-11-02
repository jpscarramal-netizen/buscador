"""Varredura completa num processo separado (não disputa o GIL com a janela); grava o cache."""
import sys
import time

from .indice import Indice, unidades_fixas, varrer


def main():
    t = time.time()
    bruto = varrer(unidades_fixas(), lambda r, n: print(f"P\t{r}\t{n}", flush=True))
    print("M", flush=True)
    idx = Indice()
    idx.montar(*bruto)
    del bruto
    idx.salvar()
    print(f"F\t{len(idx)}\t{time.time() - t:.0f}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
