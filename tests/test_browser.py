"""Posse do perfil de browser via MCP (T-022) — a colisão que originou o projeto.

O gate já recusava o KILL do processo (AC-003), mas o claim que esse ramo
consulta **nunca era adquirido**: os matchers do PreToolUse cobriam
Edit/Write/NotebookEdit e Bash, e ferramenta de MCP passa ao largo. Medido no
ensaio T-013 — o deny do cenário 4 só funcionou porque o claim foi criado à mão.

Consumidor E produtor testados: `policy` decide, o entrypoint adquire. Testar só
o primeiro é o buraco que já mordeu AC-007 e AC-010 neste projeto.
"""

try:
    from . import _guarda  # noqa: F401
except ImportError:
    import _guarda  # noqa: F401


import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(RAIZ), "src")
HOOKS = os.path.join(os.path.dirname(RAIZ), "hooks")
sys.path.insert(0, SRC)

from ccoord import claims, policy  # noqa: E402
from ccoord.classify import classify  # noqa: E402


class _SessaoFalsa:
    def __init__(self, session_id="eu", name="eu"):
        self.session_id = session_id
        self.name = name
        self.cwd = None
        self.pid = 1


class _DonoFalso:
    def __init__(self, session_id="peer-1", name="peer-viva"):
        self.session_id = session_id
        self.name = name
        self.pid = 9
        self.agent_id = None


class _ClaimFalso:
    def __init__(self):
        self.owner = _DonoFalso()
        self.range = None


class TestClassificacaoDeBrowser(unittest.TestCase):
    def test_navegar_toma_posse_do_servidor(self):
        "@spec:AC-003 ferramenta de browser do MCP vira recurso browser:<servidor>"
        r = classify("mcp__playwright__browser_navigate", {"url": "https://x"}, "")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0].kind, "browser")
        self.assertEqual(r[0].id, "browser:playwright")
        self.assertEqual(r[0].action, "use")

    def test_o_segundo_servidor_e_um_recurso_DIFERENTE(self):
        "@spec:AC-003 playwright-b é outro perfil: não pode colidir com playwright"
        # É o que torna a alternativa do aviso real — se os dois colapsassem no
        # mesmo id, mandar a peer para o `playwright-b` não resolveria nada.
        a = classify("mcp__playwright__browser_click", {}, "")[0]
        b = classify("mcp__playwright-b__browser_click", {}, "")[0]
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(b.id, "browser:playwright-b")

    def test_browser_close_libera_em_vez_de_tomar(self):
        "@spec:AC-003 browser_close é declaração de fim de uso, não mais um uso"
        r = classify("mcp__playwright__browser_close", {}, "")
        self.assertEqual(r[0].action, "release")

    def test_leitura_pura_nao_toma_posse(self):
        "@spec:AC-003 screenshot/console/snapshot não criam claim (não-vacuidade)"
        # Sem isto, a primeira sessão a espiar o browser travaria as demais.
        for ferramenta in (
            "mcp__playwright__browser_take_screenshot",
            "mcp__playwright__browser_console_messages",
            "mcp__playwright__browser_snapshot",
        ):
            with self.subTest(ferramenta=ferramenta):
                self.assertEqual(classify(ferramenta, {}, ""), [])

    def test_ferramenta_que_nao_e_de_browser_continua_ignorada(self):
        "@spec:AC-003 outras ferramentas MCP não viram recurso (não-vacuidade)"
        for nome in ("mcp__github__create_repository", "mcp__obsidian__obsidian_append_content", "Read"):
            with self.subTest(nome=nome):
                self.assertEqual(classify(nome, {}, ""), [])


class TestPoliticaDeBrowser(unittest.TestCase):
    def _usar(self, owner):
        from ccoord.classify import Resource

        return policy.decide(
            Resource(kind="browser", id="browser:playwright", action="use"),
            owner=owner,
            me=_SessaoFalsa(),
            peers=[],
            contexto={},
        )

    def test_perfil_livre_e_liberado_em_silencio(self):
        "@spec:AC-003 browser sem dono não gera atrito"
        self.assertEqual(self._usar(None).verdict, "allow")

    def test_perfil_de_peer_viva_gera_AVISO_nunca_recusa(self):
        "@spec:AC-003 usar browser de peer viva avisa e NÃO bloqueia"
        # Bloquear aqui deixaria a sessão sem saída — que é como se aprende a
        # contornar o gate. O que continua recusado é o KILL do processo.
        d = self._usar(_ClaimFalso())
        self.assertEqual(d.verdict, "warn")
        self.assertIn("peer-viva", d.reason)

    def test_o_aviso_traz_a_alternativa_CONCRETA(self):
        "@spec:AC-003 o aviso nomeia o outro servidor, não diz 'use outro perfil'"
        d = self._usar(_ClaimFalso())
        self.assertIn("playwright-b", d.reason)

    def test_o_aviso_desaconselha_o_kill_explicitamente(self):
        "@spec:AC-003 o aviso fecha a porta errada antes que ela seja tentada"
        self.assertIn("matar", self._usar(_ClaimFalso()).reason.lower())


class TestEntrypointDeBrowser(unittest.TestCase):
    """O PRODUTOR: sem ele o claim não existe e o ramo de kill fica cego."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self._tmp.name, "coord")
        self.sessions_dir = os.path.join(self._tmp.name, "sessions")
        os.makedirs(self.home)
        os.makedirs(self.sessions_dir)
        self.addCleanup(self._tmp.cleanup)

    def _rodar(self, tool_name, session_id="sessao-a"):
        env = dict(os.environ)
        env.update(
            {
                "CCOORD_HOME": self.home,
                "CCOORD_SESSIONS_DIR": self.sessions_dir,
                "CCOORD_SRC": SRC,
                "PYTHONPATH": SRC,
                "CLAUDE_CODE_SESSION_ID": session_id,
            }
        )
        env.pop("CLAUDE_PID", None)
        p = subprocess.run(
            [sys.executable, os.path.join(HOOKS, "coord_pre_browser.py")],
            input=json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": session_id,
                    "cwd": self._tmp.name,
                    "tool_name": tool_name,
                    "tool_input": {},
                }
            ),
            capture_output=True,
            text=True,
            env=env,
        )
        return p.returncode, (p.stdout or "").strip()

    def _claim_no_disco(self):
        """O claim gravado, ignorando liveness.

        `owner_of()` filtra dono morto — e no subprocesso do teste não há
        `CLAUDE_PID`, então o dono nasce com `pid=0` e seria lido como morto,
        escondendo um claim que EXISTE em disco. Em produção o harness fornece
        o PID e isso não ocorre; aqui o que se quer provar é a gravação, não a
        liveness (que tem testes próprios em test_claims.py).
        """
        os.environ["CCOORD_HOME"] = self.home
        return claims.owner_of("browser:playwright", esta_vivo=lambda _o: True)

    def test_navegar_CRIA_o_claim_em_disco(self):
        "@spec:AC-003 o entrypoint adquire o claim de verdade — não basta decidir bem"
        codigo, _ = self._rodar("mcp__playwright__browser_navigate")
        self.assertEqual(codigo, 0)
        c = self._claim_no_disco()
        self.assertIsNotNone(c, "sem claim em disco, o deny de kill nunca tem dono para citar")
        self.assertEqual(c.scope, "session", "claim de turno sumiria no Stop, no meio da navegação")

    def test_browser_close_libera_o_claim(self):
        "@spec:AC-003 browser_close solta o perfil para quem estava esperando"
        self._rodar("mcp__playwright__browser_navigate")
        self.assertIsNotNone(self._claim_no_disco())
        self._rodar("mcp__playwright__browser_close")
        self.assertIsNone(self._claim_no_disco(), "sem release, a peer espera para sempre")

    def test_leitura_pura_nao_cria_claim(self):
        "@spec:AC-003 screenshot não toma posse (não-vacuidade do entrypoint)"
        self._rodar("mcp__playwright__browser_take_screenshot")
        self.assertIsNone(self._claim_no_disco())

    def test_o_hook_sempre_sai_zero(self):
        "@spec:AC-011 o processo do hook de browser nunca derruba o turno"
        for nome in ("mcp__playwright__browser_navigate", "Read", ""):
            with self.subTest(nome=nome):
                codigo, _ = self._rodar(nome)
                self.assertEqual(codigo, 0)


if __name__ == "__main__":
    unittest.main()
