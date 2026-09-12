"""Isolamento do estado, importado no topo de CADA modulo de teste.

Por que nao basta `tests/__init__.py` (achado ALTA da 4a auditoria, 12/09):
`python -m unittest discover -s tests` **sem** `-t` trata `tests/` como
top-level-dir e carrega os modulos SOLTOS -- o pacote `tests` nunca e
importado, o `__init__.py` nunca roda, e o isolamento nao acontece. Reproduzido
duas vezes: `~/.claude/coord/events.log` foi de 45 para 47 linhas.

Este modulo e importado explicitamente pelo topo de cada `test_*.py`, o que
funciona nas DUAS formas de carregamento:
  - como pacote  (`python -m unittest tests.test_x`) -> `from . import _guarda`
  - solto        (`discover -s tests`)                -> `import _guarda`

Nao sobrescreve `CCOORD_HOME` ja definido: quem aponta de proposito continua
no controle.
"""

import atexit
import os
import tempfile

_isolado = None

if not os.environ.get("CCOORD_HOME"):
    _isolado = tempfile.TemporaryDirectory(prefix="ccoord_testes_")
    os.environ["CCOORD_HOME"] = _isolado.name

    @atexit.register
    def _limpar():
        try:
            _isolado.cleanup()
        except OSError:
            pass
