"""T-026 — a faixa do claim tem de acompanhar o turno inteiro.

O item 3 da medicao de 17/09 era "45 das 55 disputas caem na pasta de
memoria". Ao abrir, o defeito nao era o volume: era a faixa estar ERRADA.

`_renovar()` recriava o claim com `range=existente.range` — a faixa da
PRIMEIRA edicao do turno — enquanto o `events.log` registrava a faixa NOVA.
Consequencias, nas duas direcoes:

  - silencio indevido: eu edito `MEMORY.md` nas linhas 10-12 e depois nas
    80-84; o claim em disco continua dizendo 10-12. A peer que mexe na 82
    ouve "sem sobreposicao, so ciencia" — exatamente a colisao real que o
    projeto existe para pegar;
  - alarme falso: a peer que mexe na 11, onde eu ja nao estou mais, leva
    aviso forte.

E o log divergia do disco, o que envenena qualquer medicao futura (foi por
ele que a primeira leitura desta mesma investigacao passou perto do defeito
sem ve-lo).

  AC-023 — o claim guarda TODAS as faixas que o dono tocou no turno, e a
  decisao compara contra o conjunto.

`None` (arquivo inteiro, tipico de `Write`) continua colidindo com tudo: um
`Write` apaga o arquivo inteiro, e a semantica "None colide com tudo" de
`policy._ranges_overlap` e o que protege isso.
"""

from __future__ import annotations

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py).
try:
    from . import _guarda  # noqa: F401
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401


import json
import os
import shutil
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(RAIZ), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ccoord import claims, policy  # noqa: E402
from ccoord.classify import Resource  # noqa: E402

RES = "file:c--dev-memory.md"
PATH = r"C:\dev\MEMORY.md"


def _owner(session_id="A", pid=None, agent_id=None):
    return claims.Owner(
        session_id=session_id,
        pid=pid if pid is not None else os.getpid(),
        proc_start="",
        name=session_id,
        agent_id=agent_id,
    )


def _sessao(session_id="B", pid=None):
    from ccoord.sessions import Session

    return Session(
        session_id=session_id,
        pid=pid if pid is not None else os.getpid() + 1,
        proc_start="",
        name=session_id,
        cwd=r"C:\dev",
        status="busy",
        updated_at=0,
        pid_domain="",
    )


class TestFaixasDoTurno(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccoord_t026_")
        self._antigo = os.environ.get("CCOORD_HOME")
        os.environ["CCOORD_HOME"] = self.tmp

    def tearDown(self):
        if self._antigo is None:
            os.environ.pop("CCOORD_HOME", None)
        else:
            os.environ["CCOORD_HOME"] = self._antigo
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _reivindicar(self, faixa, dono=None):
        return claims.claim(
            RES,
            dono or _owner(),
            ttl_s=900,
            meta={
                "path": PATH,
                "range": list(faixa) if faixa else None,
                "scope": "turn",
                "purpose": "edit via PreToolUse (Edit)",
            },
        )

    def test_segunda_edicao_acrescenta_a_faixa(self):
        "@spec:AC-023 a segunda edicao do mesmo dono acrescenta a faixa, nao e descartada"
        self._reivindicar((10, 12))
        r = self._reivindicar((80, 84))
        self.assertTrue(r.ok)
        self.assertIn((80, 84), r.claim.ranges, f"faixa nova sumiu: {r.claim.ranges}")
        self.assertIn((10, 12), r.claim.ranges, f"faixa antiga sumiu: {r.claim.ranges}")

    def test_faixa_persiste_no_disco(self):
        "@spec:AC-023 as faixas do turno ficam no arquivo de claim, nao so em memoria"
        self._reivindicar((10, 12))
        self._reivindicar((80, 84))
        arquivos = os.listdir(os.path.join(self.tmp, "claims"))
        self.assertEqual(len(arquivos), 1)
        with open(os.path.join(self.tmp, "claims", arquivos[0]), encoding="utf-8") as fh:
            d = json.load(fh)
        self.assertIn([80, 84], d.get("ranges", []), f"disco nao tem a faixa nova: {d}")
        self.assertIn([10, 12], d.get("ranges", []), f"disco nao tem a faixa antiga: {d}")

    def test_peer_na_faixa_nova_recebe_aviso_de_colisao(self):
        "@spec:AC-023 quem edita a linha 82 e avisado de sobreposicao (era silencio indevido)"
        self._reivindicar((10, 12))
        self._reivindicar((80, 84))
        dono_claim = claims.owner_of(RES)
        recurso = Resource(kind="file", id=RES, path=PATH, action="edit", lines=(82, 82))
        d = policy.decide(recurso, dono_claim, _sessao("B"), [_sessao("A", pid=os.getpid())])
        self.assertEqual(d.verdict, "warn")
        self.assertIn(
            "colide",
            d.reason.lower(),
            f"faixa nova ignorada — a peer nao soube da colisao real: {d.reason}",
        )

    def test_peer_longe_de_todas_as_faixas_so_recebe_ciencia(self):
        "@spec:AC-023 quem edita longe de todas as faixas segue sem alarme (nao-vacuidade)"
        self._reivindicar((10, 12))
        self._reivindicar((80, 84))
        dono_claim = claims.owner_of(RES)
        recurso = Resource(kind="file", id=RES, path=PATH, action="edit", lines=(500, 505))
        d = policy.decide(recurso, dono_claim, _sessao("B"), [_sessao("A", pid=os.getpid())])
        self.assertEqual(d.verdict, "warn")
        self.assertIn("sem sobreposicao", d.reason.lower().replace("ç", "c").replace("ã", "a"))

    def test_write_continua_valendo_pelo_arquivo_inteiro(self):
        "@spec:AC-023 claim sem faixa (Write) continua colidindo com qualquer edicao"
        self._reivindicar(None)
        dono_claim = claims.owner_of(RES)
        recurso = Resource(kind="file", id=RES, path=PATH, action="edit", lines=(999, 999))
        d = policy.decide(recurso, dono_claim, _sessao("B"), [_sessao("A", pid=os.getpid())])
        self.assertEqual(d.verdict, "warn")
        self.assertIn("colide", d.reason.lower())

    def test_faixa_seguida_de_write_volta_a_valer_pelo_arquivo_inteiro(self):
        "@spec:AC-023 Edit e depois Write no mesmo turno: o claim vira arquivo inteiro"
        self._reivindicar((10, 12))
        r = self._reivindicar(None)
        self.assertTrue(r.ok)
        self.assertIsNone(r.claim.range, f"Write nao zerou a faixa: {r.claim.range}")
        dono_claim = claims.owner_of(RES)
        recurso = Resource(kind="file", id=RES, path=PATH, action="edit", lines=(999, 999))
        d = policy.decide(recurso, dono_claim, _sessao("B"), [_sessao("A", pid=os.getpid())])
        self.assertIn("colide", d.reason.lower())

    def test_log_de_renovacao_descreve_o_disco(self):
        "@spec:AC-023 o evento de renovacao registra as faixas que ficaram em disco"
        self._reivindicar((10, 12))
        self._reivindicar((80, 84))
        eventos = []
        with open(os.path.join(self.tmp, "events.log"), encoding="utf-8") as fh:
            for linha in fh:
                if linha.strip():
                    eventos.append(json.loads(linha))
        renovacoes = [e for e in eventos if e.get("renewed")]
        self.assertTrue(renovacoes, "nenhuma renovacao registrada")
        self.assertEqual(
            renovacoes[-1].get("ranges"),
            [[10, 12], [80, 84]],
            "o log nao descreve o disco — foi essa divergencia que escondeu o defeito em 17/09",
        )

    def test_ranges_malformado_nao_derruba_a_decisao(self):
        "@spec:AC-023 `ranges` corrompido degrada para `range`, sem exceção"
        # Achado da auditoria de 17/09: par sem aridade, tipo errado ou string
        # no lugar da lista atravessavam `from_dict` e só estouravam em
        # `policy.decide` — e o `hookio` engolia a exceção, então o aviso da
        # peer sumia em silêncio. Aviso que some é pior que aviso errado.
        base = {
            "resource": RES,
            "path": PATH,
            "range": [1, 2],
            "owner": {"session_id": "A", "pid": 1, "proc_start": "", "name": "A"},
            "scope": "turn",
            "purpose": "",
            "acquired_at": 0,
            "renewed_at": 0,
            "ttl_s": 900,
        }
        recurso = Resource(kind="file", id=RES, path=PATH, action="edit", lines=(5, 5))
        for malformado in ("xx", [[1]], [[1, 2, 3]], [["a", "b"]], [None], 5, {}):
            with self.subTest(ranges=repr(malformado)):
                c = claims.Claim.from_dict(dict(base, ranges=malformado))
                self.assertEqual(c.ranges, [(1, 2)], f"nao degradou para range: {c.ranges}")
                d = policy.decide(recurso, c, _sessao("B"), [_sessao("A", pid=1)])
                self.assertIn(d.verdict, ("warn", "allow"))

    def test_claim_antigo_sem_ranges_continua_legivel(self):
        "@spec:AC-023 claim gravado pela versao anterior (so `range`) segue valendo"
        self._reivindicar((10, 12))
        arquivos = os.listdir(os.path.join(self.tmp, "claims"))
        caminho = os.path.join(self.tmp, "claims", arquivos[0])
        with open(caminho, encoding="utf-8") as fh:
            d = json.load(fh)
        d.pop("ranges", None)  # formato antigo, o que esta em disco hoje
        with open(caminho, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        c = claims.owner_of(RES)
        self.assertEqual(c.range, (10, 12))
        self.assertEqual(c.ranges, [(10, 12)])


if __name__ == "__main__":
    unittest.main()
