"""Mede o filtro de alvo de escrita (`classify._alvo_de_escrita_plausivel`).

POR QUE ISTO EXISTE: a primeira versao do filtro (T-024) foi escrita por
intuicao sobre "cara de codigo" e reprovada por auditoria adversarial — ela
cegava 5,15% dos arquivos REAIS da maquina, inclusive o vault inteiro, que
nomeia nota como `Plano - portfolio GitHub (plano completo, 2026-08-25).md`.
O controle negativo usado na epoca (o proprio `events.log`) NAO podia detectar
isso: o log so contem alvos que o classificador ja produzia, entao media o
grao errado.

A tabela precisa dos DOIS lados:

  RUINS  = alvos que o classificador ja produziu e que nao existem em disco
           (fragmentos reais). Versionado em `tools/corpus_fragmentos.txt`.
  REAIS  = arquivos que EXISTEM nesta maquina. Coletado na hora, porque muda.

Regra para aceitar uma regra nova: **nao cegar nenhum arquivo real.** Ruido
irrita; cegueira faz o gate mentir dizendo que ninguem esta no arquivo.

Uso:  C:/Python314/python.exe tools/avaliar_filtro.py [raiz ...]
"""

from __future__ import annotations

import os
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(RAIZ), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ccoord import classify  # noqa: E402

RAIZES_PADRAO = [
    r"C:\Oscar Alho",
    os.path.expanduser(r"~\dev"),
    os.path.expanduser(r"~\.claude"),
    os.path.expanduser(r"~\OneDrive - HDT ENERGY"),
]
IGNORAR = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache"}


def coletar_reais(raizes: list[str]) -> list[str]:
    achados = []
    for r in raizes:
        if not os.path.isdir(r):
            continue
        for base, dirs, files in os.walk(r):
            dirs[:] = [d for d in dirs if d not in IGNORAR]
            achados.extend(os.path.join(base, f) for f in files)
    return achados


def carregar_fragmentos() -> list[str]:
    caminho = os.path.join(RAIZ, "corpus_fragmentos.txt")
    with open(caminho, encoding="utf-8") as fh:
        return [l.strip() for l in fh if l.strip()]


def main(argv: list[str]) -> int:
    raizes = argv[1:] or RAIZES_PADRAO
    ruins = carregar_fragmentos()
    reais = coletar_reais(raizes)
    if not reais:
        print("nenhum arquivo real coletado — sem o lado que nao pode ser cegado, "
              "a medida nao vale. Passe raizes existentes.")
        return 2

    rejeita = lambda a: not classify._alvo_de_escrita_plausivel(a)  # noqa: E731
    pegos = sum(1 for a in ruins if rejeita(a))
    cegos = [a for a in reais if rejeita(a)]

    print(f"fragmentos (nao existem em disco) : {len(ruins)}")
    print(f"arquivos REAIS coletados          : {len(reais)}")
    print()
    print(f"pega        {pegos}/{len(ruins)} fragmentos ({100.0 * pegos / len(ruins):.1f}%)")
    print(f"CEGA        {len(cegos)}/{len(reais)} arquivos reais "
          f"({100.0 * len(cegos) / len(reais):.4f}%)")
    if cegos:
        print()
        print("!! arquivos REAIS que o filtro rejeita — isto reprova a regra:")
        for a in cegos[:40]:
            print("   ", a)
        if len(cegos) > 40:
            print(f"    ... e mais {len(cegos) - 40}")
        return 1
    print()
    print("OK: nenhum arquivo real cegado.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
