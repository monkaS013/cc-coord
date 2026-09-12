"""Guarda contra a suite escrever no estado REAL da maquina.

ERRO de 11/09 que motivou este arquivo: o teste de "excecao do decisor" do
test_hookio.py nao isolava CCOORD_HOME, entao o registro de erro caiu no DEFAULT
(`~/.claude/coord`) e deixou 35 linhas em `~/.claude/coord/events.log` -- num
projeto cuja regra explicita era nao tocar em `~/.claude/` antes da instalacao.
O `tests/run_tap.py` passou a isolar a variavel para a suite inteira; estes
testes existem para que essa protecao nao seja removida sem alguem notar.

Sem tag @spec: nao provam critetio de aceite nenhum, sao guarda de ambiente.
"""

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py):
# sem isto, rodar este arquivo sem CCOORD_HOME grava em ~/.claude/coord real.
try:
    from . import _guarda  # noqa: F401  (import por efeito colateral)
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401



import os
import unittest
from pathlib import Path


def _home_real_do_estado() -> Path:
    return Path.home() / ".claude" / "coord"


class TestIsolamentoDoEstado(unittest.TestCase):
    def test_ccoord_home_esta_definido_durante_a_suite(self):
        "a suite roda com CCOORD_HOME definido (senao o default e o estado real)"
        self.assertTrue(
            os.environ.get("CCOORD_HOME"),
            "CCOORD_HOME nao esta definido: qualquer escrita de estado vai para "
            "~/.claude/coord, que e o estado REAL da maquina",
        )

    def test_ccoord_home_nao_aponta_para_o_estado_real(self):
        "CCOORD_HOME da suite nunca aponta para ~/.claude/coord"
        atual = Path(os.environ["CCOORD_HOME"]).resolve()
        real = _home_real_do_estado()
        try:
            real_resolvido = real.resolve()
        except OSError:  # pragma: no cover - caminho inexistente ainda
            real_resolvido = real
        self.assertNotEqual(
            atual,
            real_resolvido,
            f"a suite escreveria em {real_resolvido}, que e o estado real",
        )


if __name__ == "__main__":
    unittest.main()
