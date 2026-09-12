"""Roda a suite de testes (unittest, stdlib) e emite TAP 13.

O `onp-spec verify` cruza a tag @spec:AC-xxx com o TITULO de cada teste na saida
TAP. Em Python a tag nao cabe no nome do metodo (nao aceita @ nem :), entao ela
vive na PRIMEIRA LINHA do docstring, que e o que vira titulo aqui:

    def test_duas_sessoes_disputam(self):
        "@spec:AC-<numero> duas sessoes nao tomam o mesmo recurso"

(o exemplo acima usa <numero> de proposito: uma tag valida aqui dentro faria o
`onp-spec audit` contar ESTE arquivo como teste do criterio, que e prova falsa.)

Teste sem docstring entra no TAP pelo nome completo, e nao prova AC nenhum --
o que e o comportamento certo: prova exige tag explicita.

Uso: C:/Python314/python.exe tests/run_tap.py
"""

import os
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(RAIZ), "src")
sys.path.insert(0, SRC)


def titulo(teste):
    doc = (teste.shortDescription() or "").strip()
    return doc if doc else teste.id()


class ResultadoTap(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.linhas = []
        self._n = 0

    def _emitir(self, teste, estado, detalhe=""):
        self._n += 1
        t = titulo(teste).replace("\n", " ")
        if estado == "ok":
            self.linhas.append(f"ok {self._n} - {t}")
        elif estado == "skip":
            self.linhas.append(f"ok {self._n} - {t} # SKIP {detalhe}".rstrip())
        else:
            self.linhas.append(f"not ok {self._n} - {t}")
            for linha in detalhe.strip().splitlines():
                self.linhas.append(f"  # {linha}")

    # Todo override chama super() ANTES de emitir: sem isso, self.failures/.errors
    # ficam vazios e o exit code sai 0 mesmo com "not ok" na saida -- gate de falso
    # verde, que e pior do que nao ter gate. Achado em 11/09 por revisao.
    def addSuccess(self, teste):
        super().addSuccess(teste)
        self._emitir(teste, "ok")

    def addFailure(self, teste, err):
        super().addFailure(teste, err)
        self._emitir(teste, "not ok", self._exc_info_to_string(err, teste))

    def addError(self, teste, err):
        super().addError(teste, err)
        self._emitir(teste, "not ok", self._exc_info_to_string(err, teste))

    def addSkip(self, teste, motivo):
        super().addSkip(teste, motivo)
        self._emitir(teste, "skip", motivo)

    def addExpectedFailure(self, teste, err):
        super().addExpectedFailure(teste, err)
        self._emitir(teste, "ok")

    def addUnexpectedSuccess(self, teste):
        super().addUnexpectedSuccess(teste)
        self._emitir(teste, "not ok", "sucesso inesperado")


def main():
    # Rede de seguranca (11/09): sem CCOORD_HOME no ambiente, qualquer teste que
    # registre em events.log cai no DEFAULT, que e ~/.claude/coord -- ou seja,
    # escreve no estado REAL da maquina. Aconteceu: o teste de "excecao do decisor"
    # do test_hookio.py nao isolava a variavel e deixou 35 linhas em
    # ~/.claude/coord/events.log. Isolar aqui protege a suite inteira, presente e
    # futura, em vez de depender de cada setUp lembrar.
    isolado = None
    if not os.environ.get("CCOORD_HOME"):
        isolado = tempfile.TemporaryDirectory(prefix="ccoord_testes_")
        os.environ["CCOORD_HOME"] = isolado.name

    try:
        return _rodar()
    finally:
        if isolado is not None:
            os.environ.pop("CCOORD_HOME", None)
            try:
                isolado.cleanup()
            except OSError:
                pass


def _rodar():
    suite = unittest.defaultTestLoader.discover(start_dir=RAIZ, pattern="test_*.py")
    resultado = ResultadoTap()
    suite.run(resultado)

    print("TAP version 13")
    print(f"1..{resultado._n}")
    for linha in resultado.linhas:
        print(linha)

    falhou = len(resultado.failures) + len(resultado.errors)
    print(f"# testes {resultado._n}")
    print(f"# falhas {falhou}")
    return 1 if falhou else 0


if __name__ == "__main__":
    sys.exit(main())
