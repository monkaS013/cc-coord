"""T-025 — claim de turno nao pode sobreviver ao turno.

Medido no uso real (17/09, 5 dias de `events.log`): **3.934 claims tomados
contra 2.679 liberados** — 1.140 sem release, espalhados por TODAS as 35
sessoes (~30% do que cada uma toma), e 51 dos 76 claims em disco com mais de
24 h. Nao e sessao que morre: e sistematico.

Causa medida no codigo, nao inferida: `coord_stop.py` so libera quando
`stop_hook_active` e falso. A intencao era evitar repetir trabalho numa
reentrada — mas nesta maquina TRES hooks de Stop bloqueiam (`verify_gate`,
`delta_gate`, `obsidian_stop`), entao reentrada e rotina, e todo claim tomado
DEPOIS do primeiro Stop (isto e, durante a continuacao do turno) ficava preso
ate o TTL de 900 s ou ate um Stop futuro sem reentrada.

  AC-021 — o `Stop` libera os claims de turno TAMBEM em reentrada.
  AC-022 — o inicio de sessao varre claim orfao (dono morto/expirado), para
  que o que ja vazou nao precise esperar o TTL de ninguem.

O `release()` e idempotente e roda no fim do turno, fora do caminho quente que
o RNF-04 protege — repeti-lo custa uma varredura de diretorio e paga o preco
de nunca deixar recurso preso.
"""

from __future__ import annotations

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py).
try:
    from . import _guarda  # noqa: F401
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401


import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / "src"
HOOKS = RAIZ / "hooks"
PYTHON = sys.executable or "python"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ccoord import claims  # noqa: E402
from ccoord import sessions as _sessions_mod  # noqa: E402


def _owner(session_id="sessao-teste", pid=None, agent_id=None):
    # `proc_start=""` de proposito: sem tick armazenado, `esta_vivo_padrao` cai
    # no fail-safe "PID existe -> vivo". Com um tick INVENTADO ("1"), o dono
    # seria lido como morto e o `sweep()` comeria o claim — foi o que o teste
    # de nao-vacuidade mostrou na primeira rodada.
    return claims.Owner(
        session_id=session_id,
        pid=pid if pid is not None else os.getpid(),
        proc_start="",
        name=session_id,
        agent_id=agent_id,
    )


def _run_hook(nome, payload, home, sessions_dir):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CCOORD_HOME"] = home
    env["CCOORD_SESSIONS_DIR"] = sessions_dir
    env["CCOORD_SRC"] = str(SRC)
    proc = subprocess.run(
        [PYTHON, str(HOOKS / nome)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    return proc.returncode, proc.stdout


class TestStopLiberaEmReentrada(unittest.TestCase):
    """AC-021 — o defeito que produziu 1.140 claims presos em 5 dias."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccoord_t025_")
        self.home = os.path.join(self.tmp, "coord")
        self.sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(self.home, exist_ok=True)
        os.makedirs(self.sessions, exist_ok=True)
        self._home_antigo = os.environ.get("CCOORD_HOME")
        os.environ["CCOORD_HOME"] = self.home

    def tearDown(self):
        if self._home_antigo is None:
            os.environ.pop("CCOORD_HOME", None)
        else:
            os.environ["CCOORD_HOME"] = self._home_antigo
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _claims_em_disco(self):
        d = os.path.join(self.home, "claims")
        return [f for f in os.listdir(d)] if os.path.isdir(d) else []

    def _tomar_claim(self, sid="sessao-teste"):
        r = claims.claim(
            "file:c--dev-x.py",
            _owner(sid),
            ttl_s=900,
            meta={"path": r"C:\dev\x.py", "range": None, "scope": "turn", "purpose": "teste"},
        )
        self.assertTrue(r.ok)
        self.assertEqual(len(self._claims_em_disco()), 1)

    def test_stop_normal_libera(self):
        "@spec:AC-021 Stop sem reentrada libera os claims de turno (controle)"
        self._tomar_claim()
        rc, _ = _run_hook(
            "coord_stop.py",
            {"hook_event_name": "Stop", "session_id": "sessao-teste", "stop_hook_active": False},
            self.home,
            self.sessions,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self._claims_em_disco(), [], "claim de turno sobreviveu ao Stop")

    def test_stop_em_reentrada_faz_o_claim_expirar_rapido(self):
        "@spec:AC-021 reentrada de Stop nao deixa o claim preso: ele passa a expirar em 90s"
        self._tomar_claim()
        rc, saida = _run_hook(
            "coord_stop.py",
            {"hook_event_name": "Stop", "session_id": "sessao-teste", "stop_hook_active": True},
            self.home,
            self.sessions,
        )
        self.assertEqual(rc, 0)
        from ccoord import claims as _c

        c = _c.owner_of("file:c--dev-x.py")
        self.assertIsNotNone(c, "reentrada apagou o claim — apaga as faixas do turno em andamento")
        restante_s = (c.renewed_at + c.ttl_s * 1000 - _c._now_ms()) / 1000.0
        self.assertLessEqual(
            restante_s, 90.0, f"claim segue com TTL longo ({restante_s:.0f}s) — é o vazamento"
        )
        # AC-013: silencio no Stop e `{}` (o hookio nunca pode devolver
        # `additionalContext` aqui — foi o que gerou os 10 disparos em cadeia
        # da medicao de 11/09).
        self.assertIn(saida.strip(), ("", "{}"), f"o Stop falou: {saida!r}")
        self.assertNotIn("additionalContext", saida)

    def test_reentrada_preserva_as_faixas_do_turno(self):
        "@spec:AC-021 as faixas acumuladas no turno sobrevivem a reentrada de Stop"
        # Este é o achado da auditoria adversarial de 17/09: com `release`
        # incondicional, S2 editando onde o dono acabara de mexer ouvia "sem
        # sobreposição", porque as faixas tinham sido apagadas no meio do turno.
        from ccoord import claims as _c

        dono = _owner("sessao-teste")
        for faixa in ((1, 10), (50, 60)):
            _c.claim(
                "file:c--dev-x.py",
                dono,
                ttl_s=900,
                meta={
                    "path": r"C:\dev\x.py",
                    "range": list(faixa),
                    "scope": "turn",
                    "purpose": "edit",
                },
            )
        _run_hook(
            "coord_stop.py",
            {"hook_event_name": "Stop", "session_id": "sessao-teste", "stop_hook_active": True},
            self.home,
            self.sessions,
        )
        c = _c.owner_of("file:c--dev-x.py")
        self.assertIsNotNone(c, "o claim sumiu na reentrada")
        self.assertEqual(
            c.ranges, [(1, 10), (50, 60)], f"reentrada comeu as faixas do turno: {c.ranges}"
        )

    def test_stop_de_outra_sessao_nao_libera_o_meu(self):
        "@spec:AC-021 o Stop libera so os claims da PROPRIA sessao (nao-vacuidade)"
        self._tomar_claim(sid="sessao-A")
        rc, _ = _run_hook(
            "coord_stop.py",
            {"hook_event_name": "Stop", "session_id": "sessao-B", "stop_hook_active": True},
            self.home,
            self.sessions,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            len(self._claims_em_disco()), 1, "o Stop de B nao pode liberar claim de A"
        )


class TestSessionStartVarreOrfao(unittest.TestCase):
    """AC-022 — o que ja vazou nao espera o TTL de ninguem."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccoord_t025b_")
        self.home = os.path.join(self.tmp, "coord")
        self.sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(self.home, exist_ok=True)
        os.makedirs(self.sessions, exist_ok=True)
        self._home_antigo = os.environ.get("CCOORD_HOME")
        os.environ["CCOORD_HOME"] = self.home

    def tearDown(self):
        if self._home_antigo is None:
            os.environ.pop("CCOORD_HOME", None)
        else:
            os.environ["CCOORD_HOME"] = self._home_antigo
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _claims_em_disco(self):
        d = os.path.join(self.home, "claims")
        return [f for f in os.listdir(d)] if os.path.isdir(d) else []

    def _gravar_claim_de_sessao_morta(self):
        # PID que nao existe (alto o bastante para nao colidir) + claim ja
        # expirado: os dois sinais que `claims.sweep()` usa.
        morto = claims.Owner(
            session_id="sessao-morta",
            pid=4_000_000,
            proc_start="1",
            name="sessao-morta",
            agent_id=None,
        )
        r = claims.claim(
            "file:c--dev-orfao.py",
            morto,
            ttl_s=1,
            meta={"path": r"C:\dev\orfao.py", "range": None, "scope": "turn", "purpose": "orfao"},
        )
        self.assertTrue(r.ok)
        # envelhece o claim no disco para muito alem do TTL
        caminho = os.path.join(self.home, "claims", self._claims_em_disco()[0])
        with open(caminho, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        d["acquired_at"] = d["renewed_at"] = 1
        with open(caminho, "w", encoding="utf-8") as fh:
            json.dump(d, fh)

    def test_session_start_remove_claim_orfao(self):
        "@spec:AC-022 claim de sessao morta some no inicio da proxima sessao"
        self._gravar_claim_de_sessao_morta()
        self.assertEqual(len(self._claims_em_disco()), 1)
        rc, _ = _run_hook(
            "coord_session_start.py",
            {"hook_event_name": "SessionStart", "session_id": "nova", "source": "startup", "cwd": str(RAIZ)},
            self.home,
            self.sessions,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            self._claims_em_disco(), [], "claim orfao sobreviveu ao SessionStart"
        )

    def test_session_start_preserva_claim_vivo(self):
        "@spec:AC-022 claim de sessao VIVA e nao expirado sobrevive (nao-vacuidade)"
        r = claims.claim(
            "file:c--dev-vivo.py",
            _owner("sessao-viva"),  # pid = este processo, que esta vivo
            ttl_s=900,
            meta={"path": r"C:\dev\vivo.py", "range": None, "scope": "session", "purpose": "vivo"},
        )
        self.assertTrue(r.ok)
        rc, _ = _run_hook(
            "coord_session_start.py",
            {"hook_event_name": "SessionStart", "session_id": "nova", "source": "startup", "cwd": str(RAIZ)},
            self.home,
            self.sessions,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            len(self._claims_em_disco()), 1, "a varredura comeu claim de sessao viva"
        )


if __name__ == "__main__":
    unittest.main()
