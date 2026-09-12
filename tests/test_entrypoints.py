"""Testes dos entrypoints de hook em `hooks/` (unittest, stdlib - RNF-01).

Estes testes rodam os 7 arquivos de `hooks/` como SUBPROCESSO de verdade
(igual ao harness faria): alimentam o stdin com um payload JSON e conferem
`returncode` e o stdout. E o unico jeito de provar de verdade a regra 1 do
prompt da task ("o processo do hook SEMPRE termina com exit 0") - um teste
que importa a funcao Python e chama direto nao prova nada sobre o exit code
do PROCESSO.

Ler antes de mexer:
  - .specs/coordenacao-multissessao/medicao-hooks.md (payloads e a regra do
    exit code, secao 3: "exit 1 deixa a acao passar").
  - .specs/coordenacao-multissessao/design.md secoes 3.5, 3.7, 3.8.
  - .specs/coordenacao-multissessao/tasks.md T-09 (inclusive a "divida
    herdada da T-006" sobre porta livre).

Isolamento: cada teste usa `CCOORD_HOME`/`CCOORD_SESSIONS_DIR` proprios em
`tempfile`, nunca `~/.claude/coord` real. `CCOORD_SRC` e passado explicito
para o subprocesso (aponta para `src/` deste repo) - e a mesma variavel que a
instalacao real (T-12, ainda nao feita) vai configurar; um teste a parte
confirma que o FALLBACK (sem `CCOORD_SRC`, resolvido relativo ao proprio
arquivo do hook) tambem funciona.
"""


from __future__ import annotations

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py):
# sem isto, rodar este arquivo sem CCOORD_HOME grava em ~/.claude/coord real.
try:
    from . import _guarda  # noqa: F401  (import por efeito colateral)
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401


import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / "src"
HOOKS = RAIZ / "hooks"
PYTHON = sys.executable or "python"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ccoord import claims, classify, sessions as _sessions_mod  # noqa: E402

TODOS_OS_HOOKS = [
    "coord_session_start.py",
    "coord_pre_write.py",
    "coord_pre_bash.py",
    "coord_post_batch.py",
    "coord_file_changed.py",
    "coord_stop.py",
    "coord_session_end.py",
]

# Payload minimo, mas plausivel, para cada evento - o suficiente para o
# entrypoint rodar seu caminho normal sem lançar por chave ausente.
_PAYLOAD_BASE_POR_HOOK = {
    "coord_session_start.py": {
        "hook_event_name": "SessionStart",
        "session_id": "sessao-teste",
        "cwd": "C:\\dev\\algum-repo",
        "source": "startup",
    },
    "coord_pre_write.py": {
        "hook_event_name": "PreToolUse",
        "session_id": "sessao-teste",
        "tool_name": "Edit",
        "tool_input": {"file_path": "C:\\dev\\algum-repo\\arquivo.txt", "old_string": "a", "new_string": "b"},
        "cwd": "C:\\dev\\algum-repo",
    },
    "coord_pre_bash.py": {
        "hook_event_name": "PreToolUse",
        "session_id": "sessao-teste",
        "tool_name": "Bash",
        "tool_input": {"command": "echo ola"},
        "cwd": "C:\\dev\\algum-repo",
    },
    "coord_post_batch.py": {
        "hook_event_name": "PostToolBatch",
        "session_id": "sessao-teste",
        "tool_calls": [],
    },
    "coord_file_changed.py": {
        "hook_event_name": "FileChanged",
        "session_id": "sessao-teste",
        "file_path": "C:\\dev\\algum-repo\\arquivo.txt",
        "event": "change",
    },
    "coord_stop.py": {
        "hook_event_name": "Stop",
        "session_id": "sessao-teste",
        "stop_hook_active": False,
    },
    "coord_session_end.py": {
        "hook_event_name": "SessionEnd",
        "session_id": "sessao-teste",
        "reason": "other",
    },
}


def _run_hook(nome_arquivo: str, payload: dict, *, home: str, sessions_dir: str, ccoord_src=None, extra_env=None):
    """Roda `hooks/<nome_arquivo>` como subprocesso de verdade, com o
    payload no stdin. Devolve (returncode, stdout_texto)."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CCOORD_HOME"] = home
    env["CCOORD_SESSIONS_DIR"] = sessions_dir
    if ccoord_src is not None:
        env["CCOORD_SRC"] = ccoord_src
    else:
        env.pop("CCOORD_SRC", None)
    if extra_env:
        env.update(extra_env)

    processo = subprocess.run(
        [PYTHON, str(HOOKS / nome_arquivo)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    return processo.returncode, processo.stdout


def _unica_linha_json(saida: str) -> dict:
    linhas = [l for l in saida.splitlines() if l.strip()]
    assert len(linhas) == 1, f"esperava 1 linha de stdout, veio {len(linhas)}: {linhas!r}"
    return json.loads(linhas[0])


def _write_session_file(sessions_dir: str, pid: int, dados: dict) -> None:
    """Escreve `<sessions_dir>/<pid>.json`. Preenche `pidDomain` com o valor
    REAL desta maquina (via `ccoord.sessions._local_pid_domain()`) quando o
    chamador nao passou um -- nao e um achado do grupo "hooks", mas uma
    correcao concorrente em `ccoord.sessions` (achado #2 da 4a auditoria,
    11/09: registro SEM `pidDomain` agora e fail-closed, tratado como
    "dominio diferente" em `sessions.peers()`) tornou as fixtures antigas
    deste arquivo (que nunca setavam o campo) invisiveis como peer -- os
    testes que dependem de `_claim_de_peer_viva`/`_peers_no_repo` (git
    write, bind de porta, colisao de Edit) paravam de encontrar a peer viva
    que a propria fixture criou. Kill nao e afetado (policy._decidir_kill
    nunca consulta `peers`), por isso so ele continuava passando."""
    dados = dict(dados)
    dados.setdefault("pidDomain", _sessions_mod._local_pid_domain())
    os.makedirs(sessions_dir, exist_ok=True)
    with open(os.path.join(sessions_dir, f"{pid}.json"), "w", encoding="utf-8") as fh:
        json.dump(dados, fh)


class _AmbienteTemporario(unittest.TestCase):
    """Base comum: cria CCOORD_HOME/CCOORD_SESSIONS_DIR isolados por teste."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ccoord_test_entrypoints_")
        self.home = os.path.join(self._tmp.name, "home")
        self.sessions_dir = os.path.join(self._tmp.name, "sessions")
        os.makedirs(self.home, exist_ok=True)
        os.makedirs(self.sessions_dir, exist_ok=True)
        # os proprios helpers deste modulo (ccoord.claims) tambem respeitam
        # estas variaveis quando chamados diretamente no processo de teste.
        self._old_home = os.environ.get("CCOORD_HOME")
        self._old_sessions = os.environ.get("CCOORD_SESSIONS_DIR")
        os.environ["CCOORD_HOME"] = self.home
        os.environ["CCOORD_SESSIONS_DIR"] = self.sessions_dir

    def tearDown(self):
        for chave, valor in (
            ("CCOORD_HOME", self._old_home),
            ("CCOORD_SESSIONS_DIR", self._old_sessions),
        ):
            if valor is None:
                os.environ.pop(chave, None)
            else:
                os.environ[chave] = valor
        self._tmp.cleanup()

    def run_hook(self, nome_arquivo, payload, **kwargs):
        kwargs.setdefault("ccoord_src", str(SRC))
        return _run_hook(nome_arquivo, payload, home=self.home, sessions_dir=self.sessions_dir, **kwargs)


# ---------------------------------------------------------------------------
# Regra 1: TODO entrypoint sai com exit 0 - caminho normal, caminho de deny,
# caminho de excecao interna. Este e o bloco que protege a regra mais cara
# do prompt da task.
# ---------------------------------------------------------------------------


class TestExit0CaminhoNormal(_AmbienteTemporario):
    def test_todos_os_hooks_saem_com_exit_0_e_uma_linha_de_json(self):
        "@spec:AC-011 os 7 entrypoints sempre saem com exit 0 e stdout de uma linha JSON valida"
        for nome in TODOS_OS_HOOKS:
            with self.subTest(hook=nome):
                payload = _PAYLOAD_BASE_POR_HOOK[nome]
                codigo, saida = self.run_hook(nome, payload)
                self.assertEqual(codigo, 0, f"{nome} saiu com exit {codigo}, saida={saida!r}")
                obj = _unica_linha_json(saida)
                self.assertIsInstance(obj, dict)

    def test_payload_vazio_em_todos_os_hooks_nao_levanta(self):
        "payload {} (stdin vazio de fato) em todos os hooks sai 0 e imprime {}"
        for nome in TODOS_OS_HOOKS:
            with self.subTest(hook=nome):
                codigo, saida = self.run_hook(nome, {})
                self.assertEqual(codigo, 0)
                obj = _unica_linha_json(saida)
                self.assertEqual(obj, {})


class TestExit0CaminhoDeDeny(_AmbienteTemporario):
    """Regra 1, critica: mesmo quando a politica decide `deny` (kill de
    processo com claim de peer viva - AC-003), o PROCESSO do hook sai 0. Quem
    bloqueia de verdade e o JSON `permissionDecision:"deny"`, nao o exit
    code (medicao-hooks.md secao 3: exit != 0 em PreToolUse cai em "continue
    with tool call")."""

    def test_kill_negado_ainda_assim_sai_exit_0(self):
        "@spec:AC-003 kill de browser com claim de peer viva gera deny, mas o processo sai exit 0"
        pid_vivo = os.getpid()  # processo real, garantidamente vivo durante o teste
        dono_peer = claims.Owner(session_id="sessao-peer", pid=pid_vivo, proc_start="", name="peer-1")
        resultado = claims.claim(
            "browser:chrome.exe",
            dono_peer,
            ttl_s=3600,
            meta={"scope": "session", "purpose": "perfil de teste"},
        )
        self.assertTrue(resultado.ok)

        _write_session_file(
            self.sessions_dir,
            pid_vivo,
            {
                "pid": pid_vivo,
                "sessionId": "sessao-peer",
                "cwd": "C:\\dev\\algum-repo",
                "procStart": "",
                "status": "busy",
                "updatedAt": int(time.time() * 1000),
            },
        )

        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sessao-eu",
            "tool_name": "Bash",
            "tool_input": {"command": "taskkill /IM chrome.exe /F"},
            "cwd": "C:\\dev\\algum-repo",
        }
        codigo, saida = self.run_hook("coord_pre_bash.py", payload)

        # a regra: o PROCESSO sai 0...
        self.assertEqual(codigo, 0)
        # ...mesmo com o deny de fato presente no JSON (prova que o caminho
        # de deny foi exercitado, nao so um "allow" disfarçado).
        obj = _unica_linha_json(saida)
        hso = obj["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("peer-1", hso["permissionDecisionReason"])
        self.assertIn("NAO se pede a uma peer", hso["permissionDecisionReason"])

    def test_git_push_negado_com_peer_viva_no_mesmo_repo_sai_exit_0(self):
        "@spec:AC-007 git push com peer viva no mesmo repo gera deny, processo sai exit 0"
        repo = "C:\\dev\\workday-hdt-ts"
        pid_vivo = os.getpid()
        _write_session_file(
            self.sessions_dir,
            pid_vivo,
            {
                "pid": pid_vivo,
                "sessionId": "sessao-peer",
                "cwd": repo,
                "procStart": "",
                "status": "busy",
                "updatedAt": int(time.time() * 1000),
            },
        )
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sessao-eu",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "cwd": repo,
        }
        codigo, saida = self.run_hook("coord_pre_bash.py", payload)
        self.assertEqual(codigo, 0)
        obj = _unica_linha_json(saida)
        hso = obj["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")


class TestExit0CaminhoDeExcecaoInterna(_AmbienteTemporario):
    """Regra 7: falha interna (aqui, forcada via `CCOORD_SRC` apontando para
    um diretorio que nao existe, o que quebra o `import ccoord`) nunca
    escapa - vira silencio + exit 0, nunca traceback no stdout/stderr
    afetando o exit code."""

    def test_ccoord_src_invalido_quebra_o_import_mas_ainda_sai_exit_0(self):
        "falha ao importar ccoord (CCOORD_SRC invalido) nunca derruba o hook: exit 0, {} no stdout"
        caminho_inexistente = os.path.join(self._tmp.name, "isto-nao-existe-nunca")
        for nome in TODOS_OS_HOOKS:
            with self.subTest(hook=nome):
                payload = _PAYLOAD_BASE_POR_HOOK[nome]
                codigo, saida = self.run_hook(nome, payload, ccoord_src=caminho_inexistente)
                self.assertEqual(codigo, 0, f"{nome} nao devia derrubar o processo: saida={saida!r}")
                obj = _unica_linha_json(saida)
                self.assertEqual(obj, {})
                self.assertNotIn("Traceback", saida)


class TestBootstrapDoPathSemCcoordSrc(_AmbienteTemporario):
    def test_sem_ccoord_src_resolve_o_fallback_relativo_ao_proprio_arquivo(self):
        "regra 8: sem CCOORD_SRC, o hook resolve src/ pelo caminho relativo a ele mesmo e roda normal"
        for nome in TODOS_OS_HOOKS:
            with self.subTest(hook=nome):
                payload = _PAYLOAD_BASE_POR_HOOK[nome]
                codigo, saida = self.run_hook(nome, payload, ccoord_src=None)
                self.assertEqual(codigo, 0)
                _unica_linha_json(saida)  # so precisa nao levantar


# ---------------------------------------------------------------------------
# AC-015 / AC-016: eco filtrado e aviso de mudanca externa consumido no
# proximo PreToolUse/PostToolBatch.
# ---------------------------------------------------------------------------


class TestFileChangedEcoDaPropriaSessao(_AmbienteTemporario):
    def test_escrita_propria_seguida_de_filechanged_nao_gera_carimbo(self):
        "@spec:AC-015 FileChanged para a MESMA sessao que acabou de editar o arquivo nao registra nada"
        with tempfile.TemporaryDirectory() as d:
            alvo = os.path.join(d, "alvo.txt")
            with open(alvo, "w", encoding="utf-8") as fh:
                fh.write("linha 1\nlinha 2\n")

            # 1) sessao-a edita o arquivo -> coord_pre_write.py carimba a
            #    escrita propria em <CCOORD_HOME>/own_writes/.
            codigo, _ = self.run_hook(
                "coord_pre_write.py",
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "sessao-a",
                    "tool_name": "Edit",
                    "tool_input": {"file_path": alvo, "old_string": "linha 1", "new_string": "linha 1 mudou"},
                    "cwd": d,
                },
            )
            self.assertEqual(codigo, 0)
            own_writes_dir = os.path.join(self.home, "own_writes")
            self.assertTrue(os.path.isdir(own_writes_dir))
            self.assertEqual(len(os.listdir(own_writes_dir)), 1)

            # 2) FileChanged chega para a MESMA sessao (eco medido: o
            #    watcher dispara igual para a propria escrita) -> filtrado.
            codigo, saida = self.run_hook(
                "coord_file_changed.py",
                {
                    "hook_event_name": "FileChanged",
                    "session_id": "sessao-a",
                    "file_path": alvo,
                    "event": "change",
                },
            )
            self.assertEqual(codigo, 0)
            self.assertEqual(_unica_linha_json(saida), {})

            changed_dir = os.path.join(self.home, "changed")
            carimbos = os.listdir(changed_dir) if os.path.isdir(changed_dir) else []
            self.assertEqual(carimbos, [], "eco da propria sessao NAO pode virar carimbo de mudanca")

    def test_filechanged_de_outra_sessao_para_o_mesmo_arquivo_nao_e_filtrado(self):
        "FileChanged de uma sessao DIFERENTE da que escreveu nao e tratado como eco"
        with tempfile.TemporaryDirectory() as d:
            alvo = os.path.join(d, "alvo.txt")
            with open(alvo, "w", encoding="utf-8") as fh:
                fh.write("conteudo\n")

            self.run_hook(
                "coord_pre_write.py",
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "sessao-a",
                    "tool_name": "Write",
                    "tool_input": {"file_path": alvo, "content": "novo conteudo"},
                    "cwd": d,
                },
            )

            # sessao-b (peer) recebe o FileChanged - nao existe carimbo de
            # escrita propria PARA ELA, entao nao e eco.
            codigo, _ = self.run_hook(
                "coord_file_changed.py",
                {
                    "hook_event_name": "FileChanged",
                    "session_id": "sessao-b",
                    "file_path": alvo,
                    "event": "change",
                },
            )
            self.assertEqual(codigo, 0)

            changed_dir = os.path.join(self.home, "changed")
            self.assertTrue(os.path.isdir(changed_dir))
            self.assertEqual(len(os.listdir(changed_dir)), 1)


class TestAvisoDeMudancaExternaNoProximoEvento(_AmbienteTemporario):
    def test_post_tool_batch_reporta_e_consome_o_carimbo_pendente(self):
        "@spec:AC-016 mudanca de peer aparece no proximo PostToolBatch, com arquivo e horario, e some depois"
        with tempfile.TemporaryDirectory() as d:
            alvo = os.path.join(d, "alvo.txt")
            with open(alvo, "w", encoding="utf-8") as fh:
                fh.write("conteudo\n")

            # peer (sessao-b) mexeu no arquivo por fora - sensor carimba.
            codigo, _ = self.run_hook(
                "coord_file_changed.py",
                {
                    "hook_event_name": "FileChanged",
                    "session_id": "sessao-b",
                    "file_path": alvo,
                    "event": "change",
                },
            )
            self.assertEqual(codigo, 0)
            changed_dir = os.path.join(self.home, "changed")
            self.assertEqual(len(os.listdir(changed_dir)), 1)

            # o PROXIMO PostToolBatch (de qualquer sessao) reporta o aviso.
            codigo, saida = self.run_hook(
                "coord_post_batch.py",
                {"hook_event_name": "PostToolBatch", "session_id": "sessao-a", "tool_calls": []},
            )
            self.assertEqual(codigo, 0)
            obj = _unica_linha_json(saida)
            aviso = obj["hookSpecificOutput"]["additionalContext"]
            self.assertIn(os.path.basename(alvo), aviso)
            # tem horario (formato HH:MM:SS) no aviso, nao so o nome do arquivo.
            self.assertRegex(aviso, r"\d{2}:\d{2}:\d{2}")

            # consumido: nao aparece de novo no PostToolBatch seguinte.
            self.assertEqual(os.listdir(changed_dir), [])
            codigo2, saida2 = self.run_hook(
                "coord_post_batch.py",
                {"hook_event_name": "PostToolBatch", "session_id": "sessao-a", "tool_calls": []},
            )
            self.assertEqual(codigo2, 0)
            self.assertEqual(_unica_linha_json(saida2), {})

    def test_pre_write_tambem_reporta_e_consome_o_carimbo_do_arquivo_especifico(self):
        "@spec:AC-016 mudanca de peer no MESMO arquivo tambem aparece no proximo PreToolUse (coord_pre_write)"
        with tempfile.TemporaryDirectory() as d:
            alvo = os.path.join(d, "alvo.txt")
            with open(alvo, "w", encoding="utf-8") as fh:
                fh.write("linha 1\n")

            codigo, _ = self.run_hook(
                "coord_file_changed.py",
                {
                    "hook_event_name": "FileChanged",
                    "session_id": "sessao-b",
                    "file_path": alvo,
                    "event": "change",
                },
            )
            self.assertEqual(codigo, 0)

            codigo, saida = self.run_hook(
                "coord_pre_write.py",
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "sessao-a",
                    "tool_name": "Edit",
                    "tool_input": {"file_path": alvo, "old_string": "linha 1", "new_string": "linha 1 nova"},
                    "cwd": d,
                },
            )
            self.assertEqual(codigo, 0)
            obj = _unica_linha_json(saida)
            aviso = obj["hookSpecificOutput"]["additionalContext"]
            self.assertIn(os.path.basename(alvo), aviso)
            self.assertRegex(aviso, r"\d{2}:\d{2}:\d{2}")


# ---------------------------------------------------------------------------
# AC-008: SessionEnd libera TODAS as claims da sessao.
# ---------------------------------------------------------------------------


class TestSessionEndLiberaTudo(_AmbienteTemporario):
    def test_session_end_nao_deixa_claim_da_sessao(self):
        "@spec:AC-008 depois do SessionEnd, nenhuma claim da sessao permanece em disco"
        dono = claims.Owner(session_id="sessao-a", pid=os.getpid(), proc_start="", name="sessao-a")
        r1 = claims.claim("file:c--dev-x-app.js", dono, ttl_s=900, meta={"scope": "turn", "purpose": "edicao"})
        r2 = claims.claim("port:9100", dono, ttl_s=3600, meta={"scope": "session", "purpose": "servidor dev"})
        self.assertTrue(r1.ok and r2.ok)

        claims_dir = os.path.join(self.home, "claims")
        self.assertEqual(len(os.listdir(claims_dir)), 2)

        codigo, saida = self.run_hook(
            "coord_session_end.py",
            {"hook_event_name": "SessionEnd", "session_id": "sessao-a", "reason": "other"},
        )
        self.assertEqual(codigo, 0)
        # AC-014 de brinde: SessionEnd nunca leva hookSpecificOutput.
        self.assertEqual(_unica_linha_json(saida), {})

        self.assertEqual(os.listdir(claims_dir), [], "SessionEnd tem que liberar TODAS as claims da sessao")

    def test_session_end_nao_mexe_em_claim_de_outra_sessao(self):
        "SessionEnd de sessao-a nao libera claim que pertence a sessao-b"
        dono_a = claims.Owner(session_id="sessao-a", pid=os.getpid(), proc_start="", name="sessao-a")
        dono_b = claims.Owner(session_id="sessao-b", pid=os.getpid(), proc_start="", name="sessao-b")
        claims.claim("file:c--dev-x-a.js", dono_a, ttl_s=900, meta={"scope": "turn"})
        claims.claim("file:c--dev-x-b.js", dono_b, ttl_s=900, meta={"scope": "turn"})

        codigo, _ = self.run_hook(
            "coord_session_end.py",
            {"hook_event_name": "SessionEnd", "session_id": "sessao-a", "reason": "other"},
        )
        self.assertEqual(codigo, 0)

        claims_dir = os.path.join(self.home, "claims")
        restante = os.listdir(claims_dir)
        self.assertEqual(len(restante), 1)
        with open(os.path.join(claims_dir, restante[0]), "r", encoding="utf-8") as fh:
            dados = json.load(fh)
        self.assertEqual(dados["owner"]["session_id"], "sessao-b")


# ---------------------------------------------------------------------------
# coord_stop.py: sai calado (AC-013) e so libera claims de escopo "turn".
# ---------------------------------------------------------------------------


class TestStopSaiCaladoELiberaSoTurno(_AmbienteTemporario):
    def test_stop_nao_emite_hookspecificoutput_mesmo_com_claims_pendentes(self):
        "@spec:AC-013 Stop nao emite additionalContext nem hookSpecificOutput, so libera claims de turno"
        dono = claims.Owner(session_id="sessao-a", pid=os.getpid(), proc_start="", name="sessao-a")
        claims.claim("file:c--dev-x-turno.js", dono, ttl_s=900, meta={"scope": "turn"})
        claims.claim("port:9200", dono, ttl_s=3600, meta={"scope": "session"})

        codigo, saida = self.run_hook(
            "coord_stop.py",
            {"hook_event_name": "Stop", "session_id": "sessao-a", "stop_hook_active": False},
        )
        self.assertEqual(codigo, 0)
        self.assertEqual(_unica_linha_json(saida), {})
        self.assertNotIn("additionalContext", saida)
        self.assertNotIn("hookSpecificOutput", saida)

        claims_dir = os.path.join(self.home, "claims")
        restante = os.listdir(claims_dir)
        self.assertEqual(len(restante), 1, "so a claim de escopo 'session' pode sobrar depois do Stop")
        with open(os.path.join(claims_dir, restante[0]), "r", encoding="utf-8") as fh:
            dados = json.load(fh)
        self.assertEqual(dados["scope"], "session")

    def test_stop_hook_active_true_nao_libera_de_novo_mas_ainda_sai_0(self):
        "stop_hook_active=True (reentrada) ainda sai exit 0 e calado, so pula o release repetido"
        codigo, saida = self.run_hook(
            "coord_stop.py",
            {"hook_event_name": "Stop", "session_id": "sessao-a", "stop_hook_active": True},
        )
        self.assertEqual(codigo, 0)
        self.assertEqual(_unica_linha_json(saida), {})


# ---------------------------------------------------------------------------
# Regra 5 (divida herdada da T-006): a porta sugerida em coord_pre_bash.py e
# medida de verdade - se a proxima tambem estiver ocupada, o aviso nao pode
# citar ela.
# ---------------------------------------------------------------------------


class TestPortaLivreDeVerdade(_AmbienteTemporario):
    def test_com_a_porta_seguinte_tambem_ocupada_o_aviso_nao_cita_ela(self):
        "regra 5: porta+1 tambem ocupada -> o aviso pula para uma porta REALMENTE livre, nunca cita a ocupada"
        porta_pedida = 18099
        porta_seguinte = porta_pedida + 1

        pid_vivo = os.getpid()
        dono_peer = claims.Owner(session_id="sessao-peer", pid=pid_vivo, proc_start="", name="peer-1")
        resultado = claims.claim(
            f"port:{porta_pedida}",
            dono_peer,
            ttl_s=3600,
            meta={"scope": "session", "purpose": "servidor dev"},
        )
        self.assertTrue(resultado.ok)

        _write_session_file(
            self.sessions_dir,
            pid_vivo,
            {
                "pid": pid_vivo,
                "sessionId": "sessao-peer",
                "cwd": "C:\\dev\\algum-repo",
                "procStart": "",
                "status": "busy",
                "updatedAt": int(time.time() * 1000),
            },
        )

        bloqueador = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            bloqueador.bind(("127.0.0.1", porta_seguinte))
            bloqueador.listen(1)

            payload = {
                "hook_event_name": "PreToolUse",
                "session_id": "sessao-eu",
                "tool_name": "Bash",
                "tool_input": {"command": f"python -m http.server {porta_pedida}"},
                "cwd": "C:\\dev\\algum-repo",
            }
            codigo, saida = self.run_hook("coord_pre_bash.py", payload)
        finally:
            bloqueador.close()

        self.assertEqual(codigo, 0)
        obj = _unica_linha_json(saida)
        aviso = obj["hookSpecificOutput"]["additionalContext"]

        self.assertNotIn(str(porta_seguinte), aviso, "o aviso nao pode sugerir uma porta que esta ocupada")
        self.assertIn(str(porta_pedida), aviso)


# ---------------------------------------------------------------------------
# T-017 (RNF-04): o caminho rapido so pula `sessions.peers()` (o scan caro de
# liveness) quando NENHUM recurso do lote tem dono - precisa provar que um
# conflito REAL continua sendo detectado (nao "silencio disfarcado de
# performance") e que o caso sem conflito continua saindo calado.
# ---------------------------------------------------------------------------


class TestCaminhoRapidoNaoEngoleConflito(_AmbienteTemporario):
    def test_edit_colide_com_claim_de_peer_viva_mesmo_com_caminho_rapido(self):
        "T-017: Edit que colide com claim de peer viva ainda gera warn nomeando a peer, mesmo com sessions.peers() adiado"
        with tempfile.TemporaryDirectory() as d:
            alvo = os.path.join(d, "app.js")
            with open(alvo, "w", encoding="utf-8") as fh:
                fh.write("linha 1\nlinha 2\nlinha 3\n")

            # o mesmo `classify.classify()` que o hook usa, para a claim da
            # fixture colidir com a chave/faixa REAL (nao uma chutada a mao).
            def _ler(caminho):
                with open(caminho, "r", encoding="utf-8") as fh:
                    return fh.read()

            recursos = classify.classify(
                "Edit", {"file_path": alvo, "old_string": "linha 1"}, d, ler_arquivo=_ler
            )
            self.assertEqual(len(recursos), 1)
            recurso = recursos[0]

            pid_vivo = os.getpid()  # processo real, garantidamente vivo
            dono_peer = claims.Owner(
                session_id="sessao-peer", pid=pid_vivo, proc_start="", name="peer-editando"
            )
            resultado = claims.claim(
                recurso.id,
                dono_peer,
                ttl_s=900,
                meta={
                    "path": recurso.path,
                    "range": list(recurso.lines) if recurso.lines else None,
                    "scope": "turn",
                    "purpose": "edicao da peer",
                },
            )
            self.assertTrue(resultado.ok, f"fixture invalida: claim nao foi adquirida ({resultado.reason})")

            _write_session_file(
                self.sessions_dir,
                pid_vivo,
                {
                    "pid": pid_vivo,
                    "sessionId": "sessao-peer",
                    "cwd": d,
                    "procStart": "",
                    "status": "busy",
                    "updatedAt": int(time.time() * 1000),
                },
            )

            payload = {
                "hook_event_name": "PreToolUse",
                "session_id": "sessao-eu",
                "tool_name": "Edit",
                "tool_input": {"file_path": alvo, "old_string": "linha 1", "new_string": "linha 1 mudou"},
                "cwd": d,
            }
            codigo, saida = self.run_hook("coord_pre_write.py", payload)

            self.assertEqual(codigo, 0)
            obj = _unica_linha_json(saida)
            self.assertIn(
                "hookSpecificOutput",
                obj,
                f"o caminho rapido ENGOLIU um conflito real - isto e pior do que ser lento: {obj!r}",
            )
            aviso = obj["hookSpecificOutput"]["additionalContext"]
            self.assertIn("peer-editando", aviso)

    def test_edit_sem_claim_nenhuma_sai_calado_de_verdade(self):
        "T-017: Edit sem NENHUM claim de peer sai em silencio (allow) - o caminho rapido tem que concordar com o lento"
        with tempfile.TemporaryDirectory() as d:
            alvo = os.path.join(d, "solo.js")
            with open(alvo, "w", encoding="utf-8") as fh:
                fh.write("linha 1\n")

            payload = {
                "hook_event_name": "PreToolUse",
                "session_id": "sessao-eu",
                "tool_name": "Edit",
                "tool_input": {"file_path": alvo, "old_string": "linha 1", "new_string": "linha 1 mudou"},
                "cwd": d,
            }
            codigo, saida = self.run_hook("coord_pre_write.py", payload)

            self.assertEqual(codigo, 0)
            self.assertEqual(_unica_linha_json(saida), {})


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# T-016 / AC-007: o deny de git write cita o COMMIT que ainda nao subiu.
#
# Estes testes existem porque o AC-007 tinha prova PASS sem ter caminho real:
# o teste vivia em policy.py, que RECEBE `contexto["commits_alheios"]` pronto,
# e nenhum entrypoint preenchia esse campo. Consumidor testado, produtor
# inexistente. Aqui o git e de verdade -- repo, upstream e commit medidos.
# ---------------------------------------------------------------------------


def _git(cwd, *args, check=True):
    proc = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c", "user.email=teste@ccoord.local",
            "-c", "user.name=ccoord teste",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if check and proc.returncode != 0:
        raise unittest.SkipTest(f"git indisponivel/falhou: {args} -> {proc.stderr[:200]}")
    return proc.stdout.strip()


class TestCommitAlheioCitadoNaRazao(_AmbienteTemporario):
    def _peer_viva_no(self, repo):
        pid_vivo = os.getpid()
        _write_session_file(
            self.sessions_dir,
            pid_vivo,
            {
                "pid": pid_vivo,
                "sessionId": "sessao-peer",
                "cwd": repo,
                "procStart": "",
                "status": "busy",
                "updatedAt": int(time.time() * 1000),
            },
        )

    def _repo_com_upstream_e_commit_local(self):
        base = os.path.join(self._tmp.name, "git")
        origem = os.path.join(base, "origem")
        os.makedirs(origem, exist_ok=True)
        _git(origem, "init")
        with open(os.path.join(origem, "inicial.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        _git(origem, "add", "inicial.txt")
        _git(origem, "commit", "-m", "commit inicial")

        clone = os.path.join(base, "clone")
        _git(base, "clone", origem, clone)

        # commit LOCAL que nao existe no upstream: e este que a razao tem de citar
        with open(os.path.join(clone, "feature.txt"), "w", encoding="utf-8") as fh:
            fh.write("trabalho da peer\n")
        _git(clone, "add", "feature.txt")
        _git(clone, "commit", "-m", "feat: trabalho que nao subiu")
        hash_curto = _git(clone, "log", "-1", "--format=%h")
        return clone, hash_curto

    def test_deny_de_commit_cita_o_hash_real_do_commit_que_nao_subiu(self):
        "@spec:AC-007 o deny de git commit cita o hash real do commit ainda nao enviado ao upstream"
        clone, hash_curto = self._repo_com_upstream_e_commit_local()
        self.assertTrue(hash_curto, "fixture invalida: nao consegui medir o hash")
        self._peer_viva_no(clone)

        codigo, saida = self.run_hook(
            "coord_pre_bash.py",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sessao-eu",
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -m 'meu trabalho'"},
                "cwd": clone,
            },
        )
        self.assertEqual(codigo, 0)
        razao = _unica_linha_json(saida)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn(
            hash_curto,
            razao,
            f"o hash medido {hash_curto!r} nao aparece na razao: {razao!r}",
        )

    def test_repo_sem_upstream_recusa_sem_inventar_comparacao(self):
        "repo sem upstream: o deny continua saindo, sem citar commit nenhum (nada inventado)"
        solto = os.path.join(self._tmp.name, "solto")
        os.makedirs(solto, exist_ok=True)
        _git(solto, "init")
        with open(os.path.join(solto, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("a\n")
        _git(solto, "add", "a.txt")
        _git(solto, "commit", "-m", "unico")
        hash_local = _git(solto, "log", "-1", "--format=%h")
        self._peer_viva_no(solto)

        codigo, saida = self.run_hook(
            "coord_pre_bash.py",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "sessao-eu",
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"},
                "cwd": solto,
            },
        )
        self.assertEqual(codigo, 0)
        hso = _unica_linha_json(saida)["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        # sem upstream nao ha "commit alheio" definido: citar o hash local seria
        # inventar comparacao (o commit pode ja estar em todo lugar)
        self.assertNotIn(hash_local, hso["permissionDecisionReason"])

    def test_caminho_comum_de_edicao_nao_consulta_git(self):
        "o hook de Edit/Write nao tem consulta a git (o custo do git fica no ramo raro)"
        fonte = (HOOKS / "coord_pre_write.py").read_text(encoding="utf-8")
        self.assertNotIn("no-optional-locks", fonte)
        self.assertNotIn("_commits_alheios", fonte)
        # nao-vacuidade: o marcador existe DE FATO no hook de Bash, senao este
        # teste passaria tambem se o nome da funcao tivesse mudado
        fonte_bash = (HOOKS / "coord_pre_bash.py").read_text(encoding="utf-8")
        self.assertIn("no-optional-locks", fonte_bash)
        self.assertIn("_commits_alheios", fonte_bash)


# ---------------------------------------------------------------------------
# Achados da auditoria adversarial de 11/09 -- cada teste aqui existe porque
# algo passou verde sem estar provado.
# ---------------------------------------------------------------------------


class TestProvenienciaDoKillNoEntrypoint(_AmbienteTemporario):
    """T-020 no CAMINHO REAL, nao so no `policy`.

    O `policy` ja sabe decidir com `contexto["kill_pedido_pelo_usuario"]`, mas
    isso e o consumidor. Se nenhum entrypoint LER o transcript e preencher o
    campo, o criterio fica verde e inerte -- foi o que aconteceu com AC-007 e
    AC-010 neste mesmo projeto, duas vezes. Estes testes rodam o hook de
    verdade, com transcript de verdade.
    """

    def _monta_kill_com_dono_vivo(self):
        pid_vivo = os.getpid()
        dono = claims.Owner(session_id="sessao-peer", pid=pid_vivo, proc_start="", name="peer-1")
        self.assertTrue(claims.claim("browser:chrome.exe", dono, ttl_s=3600).ok)
        _write_session_file(
            self.sessions_dir,
            pid_vivo,
            {
                "pid": pid_vivo,
                "sessionId": "sessao-peer",
                "cwd": self._tmp.name,
                "procStart": "",
                "status": "busy",
                "updatedAt": int(time.time() * 1000),
            },
        )

    def _transcript_com(self, fala_do_usuario):
        caminho = os.path.join(self._tmp.name, "transcript.jsonl")
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"type": "user", "message": {"role": "user", "content": fala_do_usuario}})
                + "\n"
            )
        return caminho

    def _decisao(self, transcript_path):
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sessao-eu",
            "tool_name": "Bash",
            "tool_input": {"command": "taskkill /IM chrome.exe /F"},
            "cwd": self._tmp.name,
        }
        if transcript_path is not None:
            payload["transcript_path"] = transcript_path
        codigo, saida = self.run_hook("coord_pre_bash.py", payload)
        self.assertEqual(codigo, 0, "o processo do hook sempre sai 0")
        obj = _unica_linha_json(saida)
        return obj.get("hookSpecificOutput") or {}, saida

    def test_kill_nomeado_pelo_usuario_sai_como_warn_no_hook_real(self):
        "@spec:AC-018 o hook le o transcript e converte o deny em aviso quando a ordem e do usuario"
        self._monta_kill_com_dono_vivo()
        hso, saida = self._decisao(self._transcript_com("mata o chrome.exe, travou tudo"))
        self.assertNotEqual(
            hso.get("permissionDecision"),
            "deny",
            f"o usuario nomeou o alvo; deny aqui e o defeito que a T-020 corrige: {saida!r}",
        )
        contexto = hso.get("additionalContext") or hso.get("permissionDecisionReason") or ""
        self.assertIn("peer-1", contexto, "o aviso tem de dizer QUEM perde trabalho")
        self.assertNotIn("pergunte ao Vinicius", contexto)

    def test_kill_por_inferencia_minha_continua_deny_no_hook_real(self):
        "@spec:AC-009 sem o alvo nomeado pelo usuario, o hook mantem o deny (nao-vacuidade)"
        # Controle do teste acima: sem ele, um hook que parasse de negar TUDO
        # passaria no primeiro caso e ninguem veria.
        self._monta_kill_com_dono_vivo()
        hso, saida = self._decisao(self._transcript_com("esse processo parece orfao, da um jeito"))
        self.assertEqual(
            hso.get("permissionDecision"),
            "deny",
            f"kill nascido de inferencia minha tem de continuar recusado: {saida!r}",
        )

    def test_sem_transcript_o_kill_continua_deny(self):
        "@spec:AC-018 ausencia de prova nao vira autorizacao (fail-closed)"
        self._monta_kill_com_dono_vivo()
        hso, _ = self._decisao(None)
        self.assertEqual(hso.get("permissionDecision"), "deny")

    def test_saida_de_ferramenta_nao_serve_de_autorizacao(self):
        "@spec:AC-018 tool_result com o alvo nao autoriza: eu nao posso me autorizar"
        self._monta_kill_com_dono_vivo()
        caminho = os.path.join(self._tmp.name, "transcript_tool.jsonl")
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "liste"}}) + "\n")
            fh.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {"type": "tool_result", "content": "convem matar o chrome.exe"}
                            ],
                        },
                    }
                )
                + "\n"
            )
        hso, saida = self._decisao(caminho)
        self.assertEqual(
            hso.get("permissionDecision"),
            "deny",
            f"saida de ferramenta virando autorizacao e permission laundering: {saida!r}",
        )


class TestFailClosedNoKillComEstadoIlegivel(_AmbienteTemporario):
    """AC-010, metade fail-closed, no CAMINHO REAL.

    O AC-010 tinha prova PASS em tests/test_policy.py injetando
    `contexto["estado_ilegivel"]=True` na mao -- mas nenhum entrypoint
    calculava a flag. Reproduzido pela auditoria: com CCOORD_HOME apontando
    para um ARQUIVO (estado impossivel de ler), um `taskkill` real saia com
    `{}` = allow. Consumidor testado, produtor inexistente -- o mesmo padrao
    do AC-007. Estes dois testes cobrem as DUAS metades do criterio.
    """

    def _home_impossivel(self) -> str:
        caminho = os.path.join(self._tmp.name, "home_que_e_arquivo")
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write("isto e um arquivo, nao um diretorio de estado\n")
        return caminho

    def test_kill_com_estado_ilegivel_e_recusado(self):
        "@spec:AC-010 estado ilegivel + kill = deny (fail-closed), no entrypoint de verdade"
        codigo, saida = _run_hook(
            "coord_pre_bash.py",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "eu",
                "tool_name": "Bash",
                "tool_input": {"command": "taskkill /IM chrome.exe /F"},
                "cwd": self._tmp.name,
            },
            home=self._home_impossivel(),
            sessions_dir=self.sessions_dir,
            ccoord_src=str(SRC),
        )
        self.assertEqual(codigo, 0, "o processo do hook sempre sai 0")
        hso = _unica_linha_json(saida).get("hookSpecificOutput")
        self.assertIsNotNone(hso, f"esperava deny, veio silencio (=allow): {saida!r}")
        self.assertEqual(hso["permissionDecision"], "deny")
        razao = hso["permissionDecisionReason"]
        # a recusa tem de ser prescritiva: dizer o que fazer, nao so negar
        self.assertIn("Vinicius", razao)

    def test_edit_com_estado_ilegivel_e_liberado(self):
        "@spec:AC-010 estado ilegivel + Edit = allow (fail-open), no entrypoint de verdade"
        alvo = os.path.join(self._tmp.name, "arquivo.txt")
        with open(alvo, "w", encoding="utf-8") as fh:
            fh.write("linha\n")
        codigo, saida = _run_hook(
            "coord_pre_write.py",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "eu",
                "tool_name": "Edit",
                "tool_input": {"file_path": alvo, "old_string": "linha", "new_string": "outra"},
                "cwd": self._tmp.name,
            },
            home=self._home_impossivel(),
            sessions_dir=self.sessions_dir,
            ccoord_src=str(SRC),
        )
        self.assertEqual(codigo, 0)
        obj = _unica_linha_json(saida)
        decisao = (obj.get("hookSpecificOutput") or {}).get("permissionDecision")
        self.assertNotEqual(decisao, "deny", "edicao nunca pode ser bloqueada por estado ilegivel")


class TestSessionEndLiberaAteClaimDeSubagente(_AmbienteTemporario):
    """AC-008 com claim de subagente -- e o registro de um MUTANTE EQUIVALENTE.

    A auditoria adversarial trocou `scope="all"` por `scope="session"` aqui e
    a suite passou; o veredito dela foi "falta teste". Medi antes de aceitar:
    `_same_owner_identity()` so compara `agent_id` quando o owner do release
    TEM um (`if casar_agent and b.agent_id is not None`). O `SessionEnd` roda
    na thread principal, cujo owner tem `agent_id=None` -- entao "session" e
    "all" percorrem exatamente o mesmo conjunto. O mutante **e equivalente**
    nesse caminho: nao ha defeito para o teste pegar, e mutante equivalente
    nao e lacuna de cobertura.

    Este teste continua valendo pelo que ele TRAVA: o comportamento observavel
    do AC-008 com claim criada por subagente (`agent_id` distinto), que antes
    nao era exercitado por teste nenhum. Se alguem um dia passar a montar o
    owner do SessionEnd COM agent_id, a equivalencia some e este teste vira o
    que pega a regressao.
    """

    def test_claim_de_subagente_irmao_tambem_e_liberada(self):
        "@spec:AC-008 SessionEnd libera ate a claim feita por subagente (agent_id distinto)"
        from ccoord import claims as _claims

        dono_principal = _claims.Owner(session_id="sessao-x", pid=os.getpid(), proc_start="", name="eu")
        dono_subagente = _claims.Owner(
            session_id="sessao-x", pid=os.getpid(), proc_start="", name="eu", agent_id="sub-42"
        )
        r1 = _claims.claim("file:principal", dono_principal, ttl_s=900, meta={"scope": "turn"})
        r2 = _claims.claim("file:do-subagente", dono_subagente, ttl_s=900, meta={"scope": "turn"})
        self.assertTrue(r1.ok and r2.ok, "fixture invalida: as duas claims tinham de ser adquiridas")
        self.assertIsNotNone(_claims.owner_of("file:do-subagente"))

        codigo, _ = self.run_hook(
            "coord_session_end.py",
            {"hook_event_name": "SessionEnd", "session_id": "sessao-x", "reason": "other", "cwd": self._tmp.name},
        )
        self.assertEqual(codigo, 0)
        self.assertIsNone(
            _claims.owner_of("file:principal"),
            "claim da thread principal deveria ter sido liberada",
        )
        self.assertIsNone(
            _claims.owner_of("file:do-subagente"),
            "claim do SUBAGENTE ficou orfa: scope do release nao cobre agent_id",
        )


class TestFailClosedComClaimDoRecursoCorrompido(_AmbienteTemporario):
    """AC-010, segunda ilegibilidade: o DIRETORIO esta ok, o CLAIM nao.

    Achado pela 2a auditoria adversarial (11/09), depois de eu ja ter
    corrigido o caso do diretorio: `estado_legivel()` auditava so a pasta,
    entao um `.json` truncado do recurso especifico deixava `owner_of()`
    devolver None -- indistinguivel de "livre" -- e o kill saia LIBERADO
    mesmo com dono vivo. Conserto pela metade e pior que nenhum, porque
    parece resolvido.
    """

    def test_claim_corrompido_do_alvo_tambem_recusa_o_kill(self):
        "@spec:AC-010 claim do recurso ilegivel (diretorio ok) tambem da deny no kill"
        from ccoord import classify as _classify, claims as _claims

        comando = "taskkill /IM chrome.exe /F"
        recursos = _classify.classify("Bash", {"command": comando}, self._tmp.name)
        alvos = [r for r in recursos if r.kind in ("browser", "process")]
        self.assertTrue(alvos, "fixture invalida: o comando nao foi classificado como kill")

        os.makedirs(os.path.join(self.home, "claims"), exist_ok=True)
        with open(_claims._claim_file(alvos[0].id), "w", encoding="utf-8") as fh:
            fh.write('{"resource": TRUNCADO')  # JSON invalido de proposito

        # o diretorio continua perfeitamente legivel: e essa a diferenca
        self.assertTrue(_claims.estado_legivel())
        self.assertTrue(_claims.claim_ilegivel(alvos[0].id))

        codigo, saida = self.run_hook(
            "coord_pre_bash.py",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "eu",
                "tool_name": "Bash",
                "tool_input": {"command": comando},
                "cwd": self._tmp.name,
            },
        )
        self.assertEqual(codigo, 0)
        hso = _unica_linha_json(saida).get("hookSpecificOutput")
        self.assertIsNotNone(hso, f"esperava deny, veio silencio (=kill liberado): {saida!r}")
        self.assertEqual(hso["permissionDecision"], "deny")

    def test_claim_integro_e_ausente_nao_viram_falso_deny(self):
        "claim ausente ou integro nao pode virar deny por engano (nao-vacuidade do teste acima)"
        from ccoord import classify as _classify, claims as _claims

        comando = "taskkill /IM chrome.exe /F"
        alvo = [r for r in _classify.classify("Bash", {"command": comando}, self._tmp.name)
                if r.kind in ("browser", "process")][0]
        # 1) nenhum claim: livre de verdade
        self.assertFalse(_claims.claim_ilegivel(alvo.id))
        # 2) claim integro de dono morto: legivel, so nao vale
        os.makedirs(os.path.join(self.home, "claims"), exist_ok=True)
        dono = _claims.Owner(session_id="outra", pid=999999, proc_start="", name="morta")
        with open(_claims._claim_file(alvo.id), "w", encoding="utf-8") as fh:
            json.dump(
                {"resource": alvo.id, "path": alvo.id, "range": None, "owner": dono.to_dict(),
                 "scope": "session", "purpose": "teste", "acquired_at": 0, "renewed_at": 0, "ttl_s": 3600},
                fh,
            )
        self.assertFalse(
            _claims.claim_ilegivel(alvo.id),
            "claim integro nao pode ser reportado como ilegivel -- isso viraria deny em tudo",
        )


# ---------------------------------------------------------------------------
# Achados da auditoria adversarial de 11/09 (grupo "hooks") -- cada teste
# aqui existe porque um dos 6 achados listados para este grupo tinha repro
# real. Ordem: achado 1 (post_batch descarta), achado 2 (pre_write engole
# segundo warn), achados 3/4 (own_writes sem limpeza), achado 5 (CCOORD_SRC
# quebrado derruba os 7 hooks), achado 6 (janela de eco engole mudanca real).
# ---------------------------------------------------------------------------


def _slug_path_como_nos_hooks(path: str) -> str:
    """Delega para a FONTE UNICA (`ccoord.carimbos.slug_path`).

    Era uma copia da normalizacao, escrita quando os hooks ainda nao tinham
    modulo comum. A copia envelheceu e mordeu em 12/09: ao ligar a resolucao de
    nome curto 8.3 (`VINICI~1` -> `ViniciusMoraisHDT`) em `carimbos.slug_path`,
    o teste continuou gerando o slug antigo e 7 casos falharam apontando para o
    lugar errado -- o teste dizia "o aviso sumiu" quando o codigo estava certo e
    a copia e que estava velha. Teste que replica a logica que deveria checar
    nao verifica nada: ele passa junto com o bug e falha junto com a correcao.
    """
    from ccoord.carimbos import slug_path

    return slug_path(path)


class TestPostBatchNaoDescartaCarimboQueNaoCoube(_AmbienteTemporario):
    def test_carimbos_que_excedem_o_orcamento_ficam_para_o_proximo_lote(self):
        (
            "@spec:AC-016 achado 1: com muitos carimbos pendentes (mais do que "
            "cabe em additionalContext), post_batch nunca apaga um carimbo sem "
            "antes reporta-lo -- os que sobram ficam em changed/ para o proximo "
            "PostToolBatch, e nenhuma resposta cita um events.log que este hook "
            "nao escreve"
        )
        changed_dir = os.path.join(self.home, "changed")
        os.makedirs(changed_dir, exist_ok=True)
        total = 250  # paths curtos -> estoura por LINHAS (repro original: N=210)
        for i in range(total):
            dados = {
                "path": f"C:\\dev\\algum-repo\\arquivo{i:03d}.txt",
                "event": "change",
                "ts": int(time.time() * 1000),
                "session_id": "sessao-b",
            }
            with open(os.path.join(changed_dir, f"arquivo{i:03d}.json"), "w", encoding="utf-8") as fh:
                json.dump(dados, fh)

        vistos = set()
        rodadas = 0
        while True:
            codigo, saida = self.run_hook(
                "coord_post_batch.py",
                {"hook_event_name": "PostToolBatch", "session_id": "sessao-a", "tool_calls": []},
            )
            self.assertEqual(codigo, 0)
            rodadas += 1
            self.assertLess(rodadas, total, "post_batch nao esta consumindo os carimbos -- laco preso")
            obj = _unica_linha_json(saida)
            restantes = os.listdir(changed_dir)
            if obj == {} and not restantes:
                break

            aviso = obj["hookSpecificOutput"]["additionalContext"]
            self.assertNotIn(
                "events.log", aviso,
                "post_batch nao pode citar um events.log que ele mesmo nunca escreve",
            )
            # a resposta desta rodada tem que respeitar o limite REAL do
            # harness -- senao hookio._truncar cortaria por conta propria e
            # o "ver events.log" (mentiroso aqui) apareceria de qualquer jeito.
            self.assertLessEqual(len(aviso), 8000)
            self.assertLessEqual(len(aviso.splitlines()), 200)

            for i in range(total):
                nome_arquivo = f"arquivo{i:03d}.txt"
                if nome_arquivo in aviso:
                    vistos.add(nome_arquivo)

        self.assertEqual(
            len(vistos), total,
            f"carimbos perdidos sem nunca serem reportados: faltam {total - len(vistos)}",
        )
        self.assertGreater(rodadas, 1, "fixture invalida: o orcamento tinha que forcar mais de uma rodada")


class TestConflitoDePeerComCarimboExternoJuntos(_AmbienteTemporario):
    def test_os_dois_avisos_aparecem_nao_so_o_primeiro(self):
        (
            "@spec:AC-016 achado 2: colisao com claim de peer (warn) + carimbo "
            "de mudanca externa pendente (tambem warn) no MESMO arquivo -- os "
            "DOIS avisos tem que aparecer, o carimbo ja foi apagado do disco "
            "mesmo que so um deles fosse devolvido"
        )
        with tempfile.TemporaryDirectory() as d:
            alvo = os.path.join(d, "app.js")
            with open(alvo, "w", encoding="utf-8") as fh:
                fh.write("linha 1\nlinha 2\nlinha 3\n")

            def _ler(caminho):
                with open(caminho, "r", encoding="utf-8") as fh:
                    return fh.read()

            recursos = classify.classify(
                "Edit", {"file_path": alvo, "old_string": "linha 1"}, d, ler_arquivo=_ler
            )
            self.assertEqual(len(recursos), 1)
            recurso = recursos[0]

            pid_vivo = os.getpid()
            dono_peer = claims.Owner(
                session_id="sessao-peer", pid=pid_vivo, proc_start="", name="peer-editando"
            )
            resultado = claims.claim(
                recurso.id,
                dono_peer,
                ttl_s=900,
                meta={
                    "path": recurso.path,
                    "range": list(recurso.lines) if recurso.lines else None,
                    "scope": "turn",
                    "purpose": "edicao da peer",
                },
            )
            self.assertTrue(resultado.ok)
            _write_session_file(
                self.sessions_dir,
                pid_vivo,
                {
                    "pid": pid_vivo, "sessionId": "sessao-peer", "cwd": d, "procStart": "",
                    "status": "busy", "updatedAt": int(time.time() * 1000),
                },
            )

            # carimbo de mudanca externa pendente PARA O MESMO ARQUIVO --
            # simula coord_file_changed.py ja ter detectado que um TERCEIRO
            # processo tambem tocou este arquivo.
            changed_dir = os.path.join(self.home, "changed")
            os.makedirs(changed_dir, exist_ok=True)
            slug = _slug_path_como_nos_hooks(alvo)
            with open(os.path.join(changed_dir, slug + ".json"), "w", encoding="utf-8") as fh:
                json.dump(
                    {"path": alvo, "event": "change", "ts": int(time.time() * 1000),
                     "session_id": "sessao-terceiro"},
                    fh,
                )

            payload = {
                "hook_event_name": "PreToolUse",
                "session_id": "sessao-eu",
                "tool_name": "Edit",
                "tool_input": {"file_path": alvo, "old_string": "linha 1", "new_string": "linha 1 mudou"},
                "cwd": d,
            }
            codigo, saida = self.run_hook("coord_pre_write.py", payload)
            self.assertEqual(codigo, 0)
            obj = _unica_linha_json(saida)
            self.assertIn("hookSpecificOutput", obj)
            aviso = obj["hookSpecificOutput"]["additionalContext"]

            self.assertIn(
                "peer-editando", aviso,
                "o aviso de colisao com a peer sumiu quando combinado com o carimbo externo",
            )
            self.assertIn(
                "mudou em disco", aviso,
                "o aviso de mudanca externa (carimbo) sumiu -- foi consumido do disco mas nunca reportado",
            )

            # o carimbo FOI consumido -- mas so porque foi reportado junto,
            # nao porque foi descartado em silencio.
            self.assertEqual(os.listdir(changed_dir), [])


class TestOwnWritesTemValvulaDeLimpeza(_AmbienteTemporario):
    def _escrever_carimbo(self, own_dir, nome, mtime_relativo_s):
        caminho = os.path.join(own_dir, nome)
        with open(caminho, "w", encoding="utf-8") as fh:
            json.dump(
                {"path": f"C:\\dev\\x\\{nome}", "session_id": "sessao-a",
                 "ts": int(time.time() * 1000), "consumido": False},
                fh,
            )
        alvo_ts = time.time() + mtime_relativo_s
        os.utime(caminho, (alvo_ts, alvo_ts))

    def test_stop_remove_carimbos_de_escrita_propria_expirados(self):
        (
            "achados 3/4: own_writes/ nao tinha NENHUMA valvula de limpeza -- "
            "Stop tem que remover carimbos bem mais velhos que a janela de eco, "
            "sem mexer nos recentes"
        )
        own_dir = os.path.join(self.home, "own_writes")
        os.makedirs(own_dir, exist_ok=True)
        self._escrever_carimbo(own_dir, "antigo.json", -3600)  # 1h atras
        self._escrever_carimbo(own_dir, "recente.json", 0)  # agora

        codigo, _ = self.run_hook(
            "coord_stop.py",
            {"hook_event_name": "Stop", "session_id": "sessao-a", "stop_hook_active": False},
        )
        self.assertEqual(codigo, 0)

        restantes = set(os.listdir(own_dir))
        self.assertNotIn(
            "antigo.json", restantes,
            "carimbo de escrita propria expirado deveria ter sido removido pelo Stop",
        )
        self.assertIn("recente.json", restantes, "carimbo recente nao pode ser removido")

    def test_session_end_tambem_remove_carimbos_expirados(self):
        "achados 3/4: SessionEnd e a segunda camada (sessao que termina sem Stop anterior)"
        own_dir = os.path.join(self.home, "own_writes")
        os.makedirs(own_dir, exist_ok=True)
        self._escrever_carimbo(own_dir, "antigo.json", -3600)
        self._escrever_carimbo(own_dir, "recente.json", 0)

        codigo, _ = self.run_hook(
            "coord_session_end.py",
            {"hook_event_name": "SessionEnd", "session_id": "sessao-a", "reason": "other"},
        )
        self.assertEqual(codigo, 0)

        restantes = set(os.listdir(own_dir))
        self.assertNotIn("antigo.json", restantes)
        self.assertIn("recente.json", restantes)


class TestCcoordSrcApontandoParaDiretorioInexistenteCaiNoFallback(_AmbienteTemporario):
    def test_kill_ainda_e_recusado_mesmo_com_ccoord_src_quebrado(self):
        (
            "achado 5: CCOORD_SRC setado mas apontando para pasta que nao "
            "existe mais (repo renomeado/movido) tem que cair no fallback "
            "relativo -- NAO pode virar allow silencioso (bypass total da "
            "politica, inclusive o fail-closed do kill)"
        )
        caminho_quebrado = os.path.join(self._tmp.name, "isto-nao-existe-mais")

        pid_vivo = os.getpid()
        dono_peer = claims.Owner(session_id="sessao-peer", pid=pid_vivo, proc_start="", name="peer-1")
        resultado = claims.claim(
            "process:notepad.exe", dono_peer, ttl_s=3600,
            meta={"scope": "session", "purpose": "teste"},
        )
        self.assertTrue(resultado.ok)
        _write_session_file(
            self.sessions_dir, pid_vivo,
            {
                "pid": pid_vivo, "sessionId": "sessao-peer", "cwd": "C:\\dev\\algum-repo",
                "procStart": "", "status": "busy", "updatedAt": int(time.time() * 1000),
            },
        )

        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "sessao-eu",
            "tool_name": "Bash",
            "tool_input": {"command": "taskkill /IM notepad.exe /F"},
            "cwd": "C:\\dev\\algum-repo",
        }
        codigo, saida = self.run_hook("coord_pre_bash.py", payload, ccoord_src=caminho_quebrado)

        self.assertEqual(codigo, 0)
        obj = _unica_linha_json(saida)
        self.assertIn(
            "hookSpecificOutput", obj,
            f"CCOORD_SRC quebrado virou allow silencioso em vez de cair no fallback: {obj!r}",
        )
        self.assertEqual(obj["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("peer-1", obj["hookSpecificOutput"]["permissionDecisionReason"])

    def test_todos_os_7_hooks_caem_no_fallback_sem_levantar(self):
        "os 7 hooks caem no fallback relativo (nao so degradam para {} calado) quando CCOORD_SRC aponta para lixo"
        caminho_quebrado = os.path.join(self._tmp.name, "outra-pasta-que-nao-existe")
        for nome in TODOS_OS_HOOKS:
            with self.subTest(hook=nome):
                payload = _PAYLOAD_BASE_POR_HOOK[nome]
                codigo, saida = self.run_hook(nome, payload, ccoord_src=caminho_quebrado)
                self.assertEqual(codigo, 0)
                _unica_linha_json(saida)  # nao levanta


class TestEcoNaoEngoleMudancaExternaLogoApos(_AmbienteTemporario):
    def test_segunda_mudanca_real_apos_o_proprio_eco_nao_e_engolida(self):
        (
            "achado 6: depois que o proprio eco ja foi reconhecido, uma "
            "mudanca EXTERNA de verdade no mesmo arquivo (mesmo dentro da "
            "janela de 15s, mesmo session_id reportado pelo watcher) tem que "
            "virar carimbo -- nao pode ser tratada como o mesmo eco de novo"
        )
        with tempfile.TemporaryDirectory() as d:
            alvo = os.path.join(d, "alvo.txt")
            with open(alvo, "w", encoding="utf-8") as fh:
                fh.write("linha 1\n")

            codigo, _ = self.run_hook(
                "coord_pre_write.py",
                {
                    "hook_event_name": "PreToolUse",
                    "session_id": "sessao-a",
                    "tool_name": "Edit",
                    "tool_input": {"file_path": alvo, "old_string": "linha 1", "new_string": "linha 1 mudou"},
                    "cwd": d,
                },
            )
            self.assertEqual(codigo, 0)

            # o watcher dispara o FileChanged do proprio eco -> filtrado.
            codigo, _ = self.run_hook(
                "coord_file_changed.py",
                {"hook_event_name": "FileChanged", "session_id": "sessao-a", "file_path": alvo, "event": "change"},
            )
            self.assertEqual(codigo, 0)
            changed_dir = os.path.join(self.home, "changed")
            self.assertEqual(os.listdir(changed_dir) if os.path.isdir(changed_dir) else [], [])

            # uma mudanca EXTERNA DE VERDADE acontece (bypassa
            # coord_pre_write.py -- outra sessao via Bash, git checkout,
            # Notepad). own_writes/ nao e tocado por isto.

            # o watcher dispara OUTRO FileChanged, ainda dentro da janela de
            # 15s, ainda com o MESMO session_id (o watcher reporta quem esta
            # observando, nao quem editou). Isto nao pode ser filtrado de novo.
            codigo, _ = self.run_hook(
                "coord_file_changed.py",
                {"hook_event_name": "FileChanged", "session_id": "sessao-a", "file_path": alvo, "event": "change"},
            )
            self.assertEqual(codigo, 0)

            carimbos = os.listdir(changed_dir) if os.path.isdir(changed_dir) else []
            self.assertEqual(
                len(carimbos), 1,
                "a segunda mudanca (externa de verdade) tem que virar carimbo -- nao pode ser engolida como eco de novo",
            )

    def test_edicoes_rapidas_sucessivas_da_mesma_sessao_nao_geram_falso_alarme(self):
        (
            "nao-regressao: duas edicoes seguidas da MESMA sessao no mesmo "
            "arquivo continuam com os DOIS ecos filtrados (a escrita nova da "
            "credito novo -- consome-uma-vez nao pode virar alarme falso em "
            "edicao legitima)"
        )
        with tempfile.TemporaryDirectory() as d:
            alvo = os.path.join(d, "alvo.txt")
            with open(alvo, "w", encoding="utf-8") as fh:
                fh.write("linha 1\n")

            for old, new in (("linha 1", "linha 1 v2"), ("linha 1 v2", "linha 1 v3")):
                codigo, _ = self.run_hook(
                    "coord_pre_write.py",
                    {
                        "hook_event_name": "PreToolUse",
                        "session_id": "sessao-a",
                        "tool_name": "Edit",
                        "tool_input": {"file_path": alvo, "old_string": old, "new_string": new},
                        "cwd": d,
                    },
                )
                self.assertEqual(codigo, 0)

                codigo, _ = self.run_hook(
                    "coord_file_changed.py",
                    {"hook_event_name": "FileChanged", "session_id": "sessao-a", "file_path": alvo, "event": "change"},
                )
                self.assertEqual(codigo, 0)

            changed_dir = os.path.join(self.home, "changed")
            self.assertEqual(
                os.listdir(changed_dir) if os.path.isdir(changed_dir) else [],
                [],
                "duas edicoes legitimas seguidas da mesma sessao nao podem gerar falso alarme de mudanca externa",
            )
