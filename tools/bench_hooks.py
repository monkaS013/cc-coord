"""tools/bench_hooks.py - T-017: benchmark COMPLETO dos 12 cenarios de perf.

Antes (T-11), os 12 cenarios viviam todos em `tests/test_perf.py` e rodavam a
CADA execucao de `tests/run_tap.py` - N=20 execucoes x 12 cenarios, a maioria
delas subindo um subprocesso Python de verdade, o que sozinho levava a suite
inteira para mais de 2 minutos (o gesto mais frequente do projeto e "rodar a
suite"). T-017 reorganizou isto:

  - `tests/test_perf.py` mantém SO o teste de GATE (`TestGateRnf04PiorCasoP95`,
    `@spec:AC-005`) - o pior caso medido, com N execucoes suficientes para um
    p95 honesto - porque o gate PRECISA continuar rodando em toda suite (e a
    unica prova viva de que RNF-04 nao regrediu).
  - Este arquivo (`tools/bench_hooks.py`) roda os outros 11 cenarios (a grade
    de N claims x M sessoes para pre_write/pre_bash sem conflito, o cenario de
    conflito de porta, e Q-005) - o benchmark EXPLORATORIO, que informa mas
    nao precisa rodar toda hora. NAO faz parte de `tests/run_tap.py`
    (`unittest discover` so varre `tests/`, nunca `tools/`), e nao usa o
    padrao de nome `test_*.py` - nenhum dos dois motivos e acidental.

Uso:
    C:/Python314/python.exe tools/bench_hooks.py

Reaproveita TODA a infraestrutura de `tests/test_perf.py` (percentis, pool de
processos reais, fixtures de claims/sessoes/arquivo sintetico, persistencia em
JSON) por import direto do modulo - ver `_CenarioPerfBase`/`_POOL` la, que
continuam sendo a fonte da verdade (nao duplicados aqui).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TESTS_DIR = RAIZ / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import test_perf as tp  # noqa: E402 - reaproveita fixtures/infra do teste de gate

_CenarioPerfBase = tp._CenarioPerfBase
_registrar = tp._registrar
_reivindicar_para_peer = tp._reivindicar_para_peer
_gerar_arquivo_sintetico = tp._gerar_arquivo_sintetico


# ---------------------------------------------------------------------------
# Cenario base (sem conflito): grade de 3/10/30 claims x 2/10 sessoes,
# pre_write.py e pre_bash.py - a carga TIPICA (a maioria das chamadas de
# Edit/Bash nao colide com claim de peer nenhuma).
# ---------------------------------------------------------------------------


class TestPerfPreWriteSemConflito(_CenarioPerfBase):
    def _rodar(self, n_claims: int, n_sessoes: int):
        home, sessions_dir, work = self._preparar(n_claims, n_sessoes, prefixo="prewrite")
        alvo = os.path.join(work, "arquivo.js")
        old_string = _gerar_arquivo_sintetico(alvo, 50)
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sessao-eu-baseline",
            "tool_name": "Edit",
            "tool_input": {"file_path": alvo, "old_string": old_string, "new_string": old_string + " // editado"},
            "cwd": work,
        }
        tempos, _ = self._medir("coord_pre_write.py", payload, home, sessions_dir)
        _registrar(
            f"coord_pre_write.py Edit sem conflito (arquivo ~50 linhas) - {n_claims} claims, {n_sessoes} sessoes",
            tempos,
            hook="coord_pre_write.py",
            n_claims=n_claims,
            n_sessoes=n_sessoes,
            conflito=False,
        )

    def test_perf_pre_write_claims3_sessoes2(self):
        "pre_write.py sem conflito: 3 claims em disco, 2 sessoes no registro"
        self._rodar(3, 2)

    def test_perf_pre_write_claims10_sessoes2(self):
        "pre_write.py sem conflito: 10 claims em disco, 2 sessoes no registro"
        self._rodar(10, 2)

    def test_perf_pre_write_claims30_sessoes2(self):
        "pre_write.py sem conflito: 30 claims em disco, 2 sessoes no registro"
        self._rodar(30, 2)

    def test_perf_pre_write_claims10_sessoes10(self):
        "pre_write.py sem conflito: 10 claims em disco, 10 sessoes no registro"
        self._rodar(10, 10)


class TestPerfPreBashBindLivre(_CenarioPerfBase):
    def _rodar(self, n_claims: int, n_sessoes: int, porta: int):
        home, sessions_dir, work = self._preparar(n_claims, n_sessoes, prefixo="prebash")
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sessao-eu-baseline-bash",
            "tool_name": "Bash",
            "tool_input": {"command": f"python -m http.server {porta}"},
            "cwd": work,
        }
        tempos, _ = self._medir("coord_pre_bash.py", payload, home, sessions_dir)
        _registrar(
            f"coord_pre_bash.py bind porta livre, sem conflito - {n_claims} claims, {n_sessoes} sessoes",
            tempos,
            hook="coord_pre_bash.py",
            n_claims=n_claims,
            n_sessoes=n_sessoes,
            conflito=False,
        )

    def test_perf_pre_bash_claims3_sessoes2(self):
        "pre_bash.py bind sem conflito: 3 claims em disco, 2 sessoes no registro"
        self._rodar(3, 2, 19001)

    def test_perf_pre_bash_claims10_sessoes2(self):
        "pre_bash.py bind sem conflito: 10 claims em disco, 2 sessoes no registro"
        self._rodar(10, 2, 19002)

    def test_perf_pre_bash_claims30_sessoes2(self):
        "pre_bash.py bind sem conflito: 30 claims em disco, 2 sessoes no registro"
        self._rodar(30, 2, 19003)

    def test_perf_pre_bash_claims10_sessoes10(self):
        "pre_bash.py bind sem conflito: 10 claims em disco, 10 sessoes no registro"
        self._rodar(10, 10, 19004)


# ---------------------------------------------------------------------------
# Cenario com conflito de porta (AC-006): a peer detem a porta pedida E a
# porta seguinte tambem esta ocupada - forca o scan REAL de socket.bind() em
# coord_pre_bash.py (regra 5 / divida herdada da T-006) a rodar mais de uma
# iteracao. E o ramo mais caro deste hook, vale medir separado do baseline.
# ---------------------------------------------------------------------------


class TestPerfPreBashComConflito(_CenarioPerfBase):
    def test_perf_pre_bash_conflito_porta_com_vizinha_ocupada(self):
        "pre_bash.py bind com porta de peer viva e a porta seguinte tambem ocupada (pior ramo do scan de porta livre)"
        home, sessions_dir, work = self._preparar(10, 10, prefixo="prebash_conflito")
        porta = 19100
        porta_vizinha = porta + 1
        pid_peer, ticks_peer = tp._POOL.info[0]
        nome_peer = f"peer-{pid_peer}"
        _reivindicar_para_peer(home, f"port:{porta}", f"port:{porta}", None, pid_peer, ticks_peer, nome_peer)

        bloqueador = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bloqueador.bind(("127.0.0.1", porta_vizinha))
        bloqueador.listen(1)
        self.addCleanup(bloqueador.close)

        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sessao-eu-conflito-bash",
            "tool_name": "Bash",
            "tool_input": {"command": f"python -m http.server {porta}"},
            "cwd": work,
        }
        tempos, primeira_saida = self._medir("coord_pre_bash.py", payload, home, sessions_dir)

        obj = json.loads(primeira_saida)
        self.assertIn("hookSpecificOutput", obj, f"fixture nao gerou warn: {obj!r}")
        aviso = obj["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn(str(porta_vizinha), aviso, "fixture invalida: o aviso nao pode sugerir uma porta ocupada")

        _registrar(
            "coord_pre_bash.py bind COM conflito (peer viva na porta + porta seguinte tambem ocupada) - 10 claims, 10 sessoes",
            tempos,
            hook="coord_pre_bash.py",
            n_claims=10,
            n_sessoes=10,
            conflito=True,
        )


# ---------------------------------------------------------------------------
# Q-005: custo de derivar a faixa de linha do old_string - e uma leitura de
# arquivo no caminho quente. Compara arquivo pequeno (~50 linhas) com arquivo
# grande (~8000 linhas, tamanho real do web/app.js que motivou a feature).
# ---------------------------------------------------------------------------


class TestQ005CustoDaFaixaDeLinha(_CenarioPerfBase):
    def test_q005_arquivo_pequeno_50_linhas(self):
        "Q-005: Edit em arquivo pequeno (~50 linhas) - custo de derivar a faixa do old_string"
        home, sessions_dir, work = self._preparar(10, 2, prefixo="q005_pequeno")
        alvo = os.path.join(work, "pequeno.js")
        old_string = _gerar_arquivo_sintetico(alvo, 50)
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sessao-eu-q005-pequeno",
            "tool_name": "Edit",
            "tool_input": {"file_path": alvo, "old_string": old_string, "new_string": old_string + " // editado"},
            "cwd": work,
        }
        tempos, _ = self._medir("coord_pre_write.py", payload, home, sessions_dir)
        _registrar(
            "Q-005: Edit em arquivo de ~50 linhas (deriva faixa do old_string)",
            tempos,
            hook="coord_pre_write.py",
            arquivo_linhas=50,
        )

    def test_q005_arquivo_grande_8000_linhas(self):
        "Q-005: Edit em arquivo grande (~8000 linhas, tamanho real do web/app.js) - custo de derivar a faixa do old_string"
        home, sessions_dir, work = self._preparar(10, 2, prefixo="q005_grande")
        alvo = os.path.join(work, "grande.js")
        old_string = _gerar_arquivo_sintetico(alvo, 8000)
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sessao-eu-q005-grande",
            "tool_name": "Edit",
            "tool_input": {"file_path": alvo, "old_string": old_string, "new_string": old_string + " // editado"},
            "cwd": work,
        }
        tempos, _ = self._medir("coord_pre_write.py", payload, home, sessions_dir)
        _registrar(
            "Q-005: Edit em arquivo de ~8000 linhas (deriva faixa do old_string)",
            tempos,
            hook="coord_pre_write.py",
            arquivo_linhas=8000,
        )


# ---------------------------------------------------------------------------
# setUpModule/tearDownModule: `unittest` procura estes hooks no MODULO onde as
# classes de teste estao definidas (este arquivo), nao em `test_perf` - por
# isso delegamos explicitamente para `test_perf.setUpModule/tearDownModule`,
# que sao quem de fato sobe/derruba o pool de processos reais e preenche
# `test_perf._POOL` (o global que `_CenarioPerfBase._preparar` le).
# ---------------------------------------------------------------------------


def setUpModule():
    tp.setUpModule()


def tearDownModule():
    tp.tearDownModule()


def _imprimir_resumo() -> None:
    if not tp._RESULTADOS:
        return
    print()
    print("=" * 100)
    print("RESUMO (ms) - tools/bench_hooks.py")
    print("=" * 100)
    cabecalho = f"{'cenario':<90} {'p50':>7} {'p95':>7} {'max':>7}"
    print(cabecalho)
    for r in tp._RESULTADOS:
        nome = r["cenario"][:90]
        print(f"{nome:<90} {r['p50_ms']:>7.1f} {r['p95_ms']:>7.1f} {r['max_ms']:>7.1f}")
    print("=" * 100)
    print(f"Resultados brutos em: {tp._resultado_path()}")


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    resultado = runner.run(suite)
    _imprimir_resumo()
    return 0 if resultado.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
