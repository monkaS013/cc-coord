"""Achados da 5ª auditoria adversarial (14/09) — todos no ramo de KILL e escrita.

Os quatro tinham o mesmo modo de falha: o comando escapava do classificador e
`coord_pre_bash.py` fazia allow total, porque `recursos == []` pula a árvore de
decisão inteira. Não é warn relaxado — é gate ausente.
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
from ccoord.classify import Resource, classify  # noqa: E402


def _ids(comando, cwd=r"C:\dev\repo"):
    return [(r.kind, r.id, r.action) for r in classify("Bash", {"command": comando}, cwd)]


class TestKillPorCimEWmi(unittest.TestCase):
    """`wmic` está DEPRECADO no Windows 11; o caminho atual é CIM."""

    def test_invoke_cimmethod_terminate_e_kill(self):
        "@spec:AC-003 Get-CimInstance | Invoke-CimMethod Terminate é reconhecido como kill"
        r = _ids(
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
            "| Invoke-CimMethod -MethodName Terminate"
        )
        self.assertTrue(r, "kill por CIM saindo como [] = allow total no hook")
        self.assertEqual(r[0][2], "kill")
        self.assertIn("chrome", r[0][1], "o alvo vem do -Filter Name='...'")

    def test_wmiobject_terminate_e_kill(self):
        "@spec:AC-003 o Get-WmiObject().Terminate() antigo também"
        r = _ids("(Get-WmiObject Win32_Process -Filter \"Name='msedge.exe'\").Terminate()")
        self.assertTrue(r)
        self.assertEqual(r[0][2], "kill")

    def test_consultar_sem_matar_nao_e_kill(self):
        "@spec:AC-003 Get-CimInstance de leitura não vira kill (não-vacuidade)"
        # O próprio projeto usa Get-CimInstance Win32_Process para CONTAR
        # processo; se isso virasse kill, todo diagnóstico geraria atrito.
        for inocente in (
            "Get-CimInstance Win32_Process",
            "Get-CimInstance Win32_Process | Select-Object Name, CreationDate",
            "Get-Process chrome",
        ):
            with self.subTest(comando=inocente):
                self.assertEqual(_ids(inocente), [])


class TestKillPorGetProcessPipe(unittest.TestCase):
    """O idioma mais comum de PowerShell — e o que saía liberado."""

    def test_get_process_pipe_stop_process_casa_com_o_claim_real(self):
        "@spec:AC-003 `Get-Process chrome | Stop-Process` vira browser:chrome, não process:desconhecido"
        # O id antigo (`process:desconhecido`) nunca casava com o claim real
        # (`browser:chrome`), então owner_of devolvia None = livre e o kill saía.
        r = _ids("Get-Process chrome | Stop-Process -Force")
        self.assertEqual(r, [("browser", "browser:chrome", "kill")])

    def test_variante_com_parenteses_e_kill(self):
        "@spec:AC-003 `(Get-Process chrome).Kill()` também"
        r = _ids("(Get-Process chrome).Kill()")
        self.assertEqual(r[0][1], "browser:chrome")


class TestKillSemAlvoIdentificavelEFailClosed(unittest.TestCase):
    def test_alvo_nao_identificado_gera_id_sentinela(self):
        "@spec:AC-010 kill cujo alvo o parser não entende não vira 'desconhecido' silencioso"
        r = _ids("taskkill /F")
        self.assertEqual(r[0][1], "process:alvo-nao-identificado")

    def test_a_politica_RECUSA_alvo_nao_identificado(self):
        "@spec:AC-010 sem saber O QUE morre, a decisão é deny — recusar por ignorância"
        d = policy.decide(
            Resource(kind="process", id="process:alvo-nao-identificado", action="kill"),
            owner=None,  # justamente: nenhum claim casa com um id que não existe
            me=None,
            peers=[],
            contexto={},
        )
        self.assertEqual(
            d.verdict,
            "deny",
            "era assim que se matava processo de peer: escrever o comando de um "
            "jeito que o parser não entendesse",
        )
        self.assertIn("Vinicius", d.reason)

    def test_kill_com_alvo_claro_e_sem_dono_continua_liberado(self):
        "@spec:AC-010 o fail-closed não pode virar recusa geral de kill (não-vacuidade)"
        d = policy.decide(
            Resource(kind="process", id="process:4242", action="kill"),
            owner=None,
            me=None,
            peers=[],
            contexto={},
        )
        self.assertEqual(d.verdict, "allow")


class TestEscritaDentroDeSubshell(unittest.TestCase):
    """Bastava embrulhar o comando para furar o gate."""

    def test_redirecionamento_dentro_de_subshell_e_detectado(self):
        "@spec:AC-005 escrita dentro de cmd /c, powershell -Command e bash -c conta"
        for comando in (
            'powershell -Command "Get-Date > compartilhado.txt"',
            "bash -c 'echo x >> compartilhado.txt'",
            'cmd /c "echo x > compartilhado.txt"',
        ):
            with self.subTest(comando=comando):
                arquivos = [r for r in _ids(comando) if r[0] == "file"]
                self.assertEqual(len(arquivos), 1, f"{comando} escapou do gate")
                self.assertIn("compartilhado.txt", arquivos[0][1])

    def test_sed_e_setcontent_dentro_de_subshell_tambem(self):
        "@spec:AC-005 os outros detectores também valem dentro do subshell"
        for comando in (
            "bash -c 'sed -i s/a/b/ alvo.txt'",
            'powershell -Command "Set-Content -Path alvo.txt -Value x"',
        ):
            with self.subTest(comando=comando):
                self.assertTrue([r for r in _ids(comando) if r[0] == "file"], comando)

    def test_subshell_sem_escrita_nao_vira_claim(self):
        "@spec:AC-005 subshell inocente segue em silêncio (não-vacuidade)"
        for inocente in (
            'powershell -Command "Get-Date"',
            "bash -c 'ls -la'",
            'cmd /c "echo ola"',
        ):
            with self.subTest(comando=inocente):
                self.assertEqual([r for r in _ids(inocente) if r[0] == "file"], [])

    def test_o_arquivo_do_subshell_e_o_MESMO_id_de_fora(self):
        "@spec:AC-005 embrulhar o comando não pode gerar um id diferente"
        # Se o id divergisse, o claim de dentro não protegeria contra a escrita
        # de fora — seria o bug do nome curto 8.3 de novo, por outra via.
        dentro = [r for r in _ids('cmd /c "echo x > a.txt"') if r[0] == "file"]
        fora = [r for r in _ids("echo x > a.txt") if r[0] == "file"]
        self.assertEqual(dentro[0][1], fora[0][1])


class TestEntrypointDeBrowserComPeerDona(unittest.TestCase):
    """O gap de cobertura que a auditoria provou com mutante.

    Nenhum dos 4 cenários do TestEntrypointDeBrowser começava com claim de peer
    JÁ existente — então remover a guarda `atual is None` do hook deixava a
    suíte inteira verde, com o aviso de colisão desaparecido.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self._tmp.name, "coord")
        self.sessions_dir = os.path.join(self._tmp.name, "sessions")
        os.makedirs(self.home)
        os.makedirs(self.sessions_dir)
        self.addCleanup(self._tmp.cleanup)
        os.environ["CCOORD_HOME"] = self.home

    def test_navegar_com_peer_dona_do_perfil_produz_o_aviso(self):
        "@spec:AC-003 o entrypoint avisa quando o perfil já é de uma peer viva"
        pid_vivo = os.getpid()
        dono = claims.Owner(
            session_id="sessao-peer", pid=pid_vivo, proc_start="", name="peer-browser"
        )
        self.assertTrue(
            claims.claim("browser:playwright", dono, ttl_s=3600, meta={"scope": "session"}).ok
        )
        with open(os.path.join(self.sessions_dir, f"{pid_vivo}.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "pid": pid_vivo,
                    "sessionId": "sessao-peer",
                    "cwd": self._tmp.name,
                    "procStart": "",
                    "status": "busy",
                    "updatedAt": int(time.time() * 1000),
                },
                fh,
            )

        env = dict(os.environ)
        env.update(
            {
                "CCOORD_HOME": self.home,
                "CCOORD_SESSIONS_DIR": self.sessions_dir,
                "CCOORD_SRC": SRC,
                "PYTHONPATH": SRC,
                "CLAUDE_CODE_SESSION_ID": "sessao-b",
            }
        )
        env.pop("CLAUDE_PID", None)
        p = subprocess.run(
            [sys.executable, os.path.join(HOOKS, "coord_pre_browser.py")],
            input=json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "sessao-b",
                    "cwd": self._tmp.name,
                    "tool_name": "mcp__playwright__browser_navigate",
                    "tool_input": {"url": "https://x"},
                }
            ),
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(p.returncode, 0)
        saida = (p.stdout or "").strip()
        self.assertNotEqual(saida, "{}", "silêncio aqui = a peer perde o browser sem aviso")
        self.assertIn("peer-browser", saida, "o aviso tem de nomear quem está usando")
        self.assertIn("playwright-b", saida, "e dar a alternativa concreta")


if __name__ == "__main__":
    unittest.main()
