"""Pacote de testes -- existe para ISOLAR o estado antes de qualquer teste rodar.

Por que este arquivo existe (12/09): `CCOORD_HOME` tem default apontando para
`~/.claude/coord`, que e o estado REAL da maquina. `tests/run_tap.py` ja isolava
a variavel, mas so protege quem roda a suite por ele -- e os agentes que
corrigiram os achados rodaram `python -m unittest tests.test_<modulo>` direto.
Resultado medido: `~/.claude/coord/claims/` criado e `events.log` de 35 para 45
linhas, num projeto cuja regra explicita e nao tocar em `~/.claude/` antes da
instalacao.

`python -m unittest tests.test_x` importa o pacote `tests` antes de carregar o
modulo, entao o isolamento aqui pega essa forma -- mas **NAO pega todas**:
`python -m unittest discover -s tests` SEM `-t` trata `tests/` como top-level-dir
e carrega os modulos SOLTOS, sem nunca importar o pacote. Isso foi medido em
12/09 (achado da 4a auditoria): o events.log real foi de 45 para 47 linhas.
Por isso existe tambem `tests/_guarda.py`, importado no TOPO de cada
`test_*.py` -- e ele que fecha o buraco. Este `__init__.py` continua valendo
como primeira linha de defesa para quem roda pelo pacote.

Nao sobrescreve `CCOORD_HOME` quando ela ja vem definida -- quem aponta de
proposito (o proprio run_tap.py, ou um teste que quer um diretorio especifico)
continua no controle.
"""

import atexit
import os
import tempfile

if not os.environ.get("CCOORD_HOME"):
    _isolado = tempfile.TemporaryDirectory(prefix="ccoord_testes_pkg_")
    os.environ["CCOORD_HOME"] = _isolado.name

    @atexit.register
    def _limpar_isolamento():
        try:
            _isolado.cleanup()
        except OSError:
            pass
