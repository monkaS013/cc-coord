"""Testes de ccoord.install - instalador dos hooks (T-12), unittest stdlib.

RESTRICAO CRITICA (ver prompt da task): nada aqui pode tocar em
`C:/Users/ViniciusMoraisHDT/.claude/*` de verdade -- todo teste roda contra um
`destino_config` em `tempfile`, nunca contra o `~/.claude` real. A fixture de
`settings.json` abaixo REPRODUZ o formato do arquivo real (lido, so leitura,
antes de escrever este teste) para provar que o merge preserva os hooks ja em
uso: `verify_gate.py`, `context_alert.py`, `memory_recall_start.py`,
`obsidian_stop.py`, `pre_push_migration_gate.py`, `block_env_edit.py`,
inclusive o caso de evento com MAIS DE UM hook (`PreToolUse` com dois grupos
de matcher, `SessionStart`/`Stop` com varios hooks no mesmo grupo).

A tag `@spec:AC-012` vai no teste que prova que a instalacao registra o hook
do mapa de coordenacao (`coord_session_start.py` -> `SessionStart`) e que o
entrypoint fica de fato disponivel em disco depois da instalacao (RF-06).
"""


from __future__ import annotations

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py):
# sem isto, rodar este arquivo sem CCOORD_HOME grava em ~/.claude/coord real.
try:
    from . import _guarda  # noqa: F401  (import por efeito colateral)
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401


import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ccoord import install  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture do settings.json REAL (reproduzida a partir da leitura, so leitura,
# de C:/Users/ViniciusMoraisHDT/.claude/settings.json em 11/09) -- inclui
# eventos com mais de um hook e PreToolUse com dois grupos de matcher.
# ---------------------------------------------------------------------------


def _settings_real_fixture() -> dict:
    return {
        "env": {
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "80",
            "DISABLE_AUTOUPDATER": "1",
            "DO_NOT_TRACK": "1",
        },
        "permissions": {
            "allow": ["Read", "Edit(*)", "Write(*)"],
            "deny": ["Bash(npx impeccable *)"],
            "ask": ["Bash(rm *)", "Bash(git push*)"],
            "defaultMode": "auto",
        },
        "model": "claude-opus-5",
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'python "C:/Users/ViniciusMoraisHDT/.claude/hooks/block_env_edit.py"',
                            "shell": "bash",
                            "timeout": 10,
                        }
                    ],
                },
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'python "C:/Users/ViniciusMoraisHDT/.claude/hooks/pre_push_migration_gate.py"',
                            "shell": "bash",
                            "timeout": 20,
                            "statusMessage": "Gate de migration no push...",
                        }
                    ],
                },
            ],
            "UserPromptSubmit": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'python "C:/Users/ViniciusMoraisHDT/.claude/hooks/context_alert.py"',
                            "shell": "bash",
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'python "C:/Users/ViniciusMoraisHDT/.claude/hooks/verify_gate.py"',
                            "shell": "bash",
                            "timeout": 15,
                        },
                        {
                            "type": "command",
                            "command": 'python "C:/Users/ViniciusMoraisHDT/.claude/hooks/obsidian_stop.py"',
                            "shell": "bash",
                            "timeout": 15,
                        },
                        {
                            "type": "command",
                            "command": 'node "C:/Users/ViniciusMoraisHDT/.claude/skills/impeccable/scripts/hook.mjs"',
                            "timeout": 30,
                        },
                    ],
                }
            ],
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'python "C:/Users/ViniciusMoraisHDT/.claude/hooks/memory_recall_start.py"',
                            "shell": "bash",
                            "timeout": 10,
                        },
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Edit|Write",
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'node "C:/Users/ViniciusMoraisHDT/.claude/skills/impeccable/scripts/hook.mjs"',
                            "timeout": 10,
                        }
                    ],
                }
            ],
        },
        "statusLine": {"type": "command", "command": "python statusline.py"},
        "language": "portuguese",
    }


# ---------------------------------------------------------------------------
# Fixture de "repo" (src_repo): so precisa ter os arquivos que instalar() lê
# e copia -- conteudo sintetico, nao precisa ser executavel de verdade (este
# modulo so copia bytes, nunca importa/roda os hooks).
# ---------------------------------------------------------------------------


def _montar_repo_fixture(base: str) -> str:
    repo = os.path.join(base, "cc-coord-fixture")
    hooks_dir = os.path.join(repo, "hooks")
    rules_dir = os.path.join(repo, "rules")
    os.makedirs(hooks_dir, exist_ok=True)
    os.makedirs(rules_dir, exist_ok=True)
    os.makedirs(os.path.join(repo, "src", "ccoord"), exist_ok=True)

    for spec in install.HOOKS_SPECS:
        with open(os.path.join(hooks_dir, spec["script"]), "w", encoding="utf-8") as fh:
            fh.write(f"# fixture do entrypoint {spec['script']}\n")

    with open(os.path.join(rules_dir, install.RULE_FILENAME), "w", encoding="utf-8") as fh:
        fh.write("# fixture da regra de coordenacao entre sessoes\n")

    return repo


class _AmbienteTemporario(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ccoord_test_install_")
        self.destino = os.path.join(self._tmp.name, "config-destino")
        os.makedirs(self.destino, exist_ok=True)
        self.repo = _montar_repo_fixture(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _escrever_settings(self, dados: dict) -> str:
        caminho = os.path.join(self.destino, "settings.json")
        with open(caminho, "w", encoding="utf-8") as fh:
            json.dump(dados, fh, indent=2, ensure_ascii=False)
        return caminho

    def _ler_settings(self) -> dict:
        caminho = os.path.join(self.destino, "settings.json")
        with open(caminho, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _contar_nossos(self, settings: dict, script: str) -> int:
        total = 0
        for grupos in settings.get("hooks", {}).values():
            for grupo in grupos:
                for entrada in grupo.get("hooks", []):
                    if script in entrada.get("command", ""):
                        total += 1
        return total


# ---------------------------------------------------------------------------
# Merge preserva hooks de terceiros (regra 1)
# ---------------------------------------------------------------------------


class TestMergePreservaTerceiros(_AmbienteTemporario):
    def test_merge_preserva_todos_os_hooks_alheios(self):
        "@spec:AC-012 instalacao acrescenta os 9 hooks do cc-coord e preserva os hooks de terceiros ja em uso"
        original = _settings_real_fixture()
        self._escrever_settings(original)

        relatorio = install.instalar(self.destino, self.repo, dry_run=False)

        self.assertTrue(relatorio.ok, relatorio.erro)
        pos = self._ler_settings()

        # --- hooks de terceiros continuam TODOS presentes, intactos -------
        terceiros_esperados = [
            "block_env_edit.py",
            "pre_push_migration_gate.py",
            "context_alert.py",
            "verify_gate.py",
            "obsidian_stop.py",
            "memory_recall_start.py",
            "hook.mjs",
        ]
        texto_pos = json.dumps(pos)
        for nome in terceiros_esperados:
            self.assertIn(nome, texto_pos, f"hook de terceiro {nome} sumiu do merge")

        # Stop tinha 3 hooks no MESMO grupo -- continuam os 3, mais o nosso.
        grupo_stop = pos["hooks"]["Stop"][0]
        self.assertEqual(len(grupo_stop["hooks"]), 4)

        # PreToolUse Edit|Write (block_env_edit) nao ganhou nada extra --
        # nosso coord_pre_write.py usa matcher DIFERENTE (Edit|Write|NotebookEdit).
        grupos_pre = pos["hooks"]["PreToolUse"]
        grupo_edit_write = next(g for g in grupos_pre if g["matcher"] == "Edit|Write")
        self.assertEqual(len(grupo_edit_write["hooks"]), 1)
        self.assertIn("block_env_edit.py", grupo_edit_write["hooks"][0]["command"])

        # PreToolUse Bash (pre_push_migration_gate) ganhou o coord_pre_bash.py
        # NO MESMO grupo (mesmo matcher) -- 2 hooks agora.
        grupo_bash = next(g for g in grupos_pre if g["matcher"] == "Bash")
        self.assertEqual(len(grupo_bash["hooks"]), 2)
        comandos_bash = [h["command"] for h in grupo_bash["hooks"]]
        self.assertTrue(any("pre_push_migration_gate.py" in c for c in comandos_bash))
        self.assertTrue(any("coord_pre_bash.py" in c for c in comandos_bash))

        # novo grupo para coord_pre_write.py (matcher proprio)
        grupo_write = next(g for g in grupos_pre if g["matcher"] == "Edit|Write|NotebookEdit")
        self.assertIn("coord_pre_write.py", grupo_write["hooks"][0]["command"])

        # --- AC-012: o mapa (coord_session_start.py) fica REGISTRADO em
        # SessionStart e o entrypoint existe de fato em disco -------------
        grupo_session_start = pos["hooks"]["SessionStart"][0]
        comandos_ss = [h["command"] for h in grupo_session_start["hooks"]]
        self.assertTrue(any("coord_session_start.py" in c for c in comandos_ss))
        self.assertTrue(any("memory_recall_start.py" in c for c in comandos_ss))  # terceiro intacto

        caminho_entrypoint = os.path.join(self.destino, "hooks", "coord_session_start.py")
        self.assertTrue(os.path.isfile(caminho_entrypoint), "entrypoint do mapa nao foi copiado")

        # eventos totalmente novos (nao existiam antes) foram criados
        self.assertIn("PostToolBatch", pos["hooks"])
        self.assertIn("FileChanged", pos["hooks"])
        self.assertIn("SessionEnd", pos["hooks"])

        # CCOORD_SRC apontando para o src do repo fixture
        self.assertIn("CCOORD_SRC", pos["env"])
        self.assertTrue(pos["env"]["CCOORD_SRC"].endswith("/src") or pos["env"]["CCOORD_SRC"].endswith("src"))

        # env de terceiro preservado
        self.assertEqual(pos["env"]["DO_NOT_TRACK"], "1")
        # permissions preservado
        self.assertEqual(pos["permissions"]["defaultMode"], "auto")


# ---------------------------------------------------------------------------
# Idempotencia (regra 5)
# ---------------------------------------------------------------------------


class TestIdempotencia(_AmbienteTemporario):
    def test_instalar_duas_vezes_nao_duplica_nenhum_hook(self):
        "instalar duas vezes seguidas nao duplica nenhum dos 9 hooks do cc-coord"
        self._escrever_settings(_settings_real_fixture())

        r1 = install.instalar(self.destino, self.repo, dry_run=False)
        self.assertTrue(r1.ok, r1.erro)
        self.assertEqual(len(r1.hooks_adicionados), len(install.HOOKS_SPECS))
        self.assertEqual(len(r1.hooks_ja_presentes), 0)

        r2 = install.instalar(self.destino, self.repo, dry_run=False)
        self.assertTrue(r2.ok, r2.erro)
        self.assertEqual(len(r2.hooks_adicionados), 0, "segunda instalacao nao deveria adicionar nada de novo")
        self.assertEqual(len(r2.hooks_ja_presentes), len(install.HOOKS_SPECS))

        pos = self._ler_settings()
        for spec in install.HOOKS_SPECS:
            self.assertEqual(
                self._contar_nossos(pos, spec["script"]),
                1,
                f"{spec['script']} apareceu mais de uma vez apos 2 instalacoes",
            )

        # hooks de terceiros tambem nao duplicaram
        self.assertEqual(self._contar_nossos(pos, "block_env_edit.py"), 1)
        grupo_stop = pos["hooks"]["Stop"][0]
        self.assertEqual(len(grupo_stop["hooks"]), 4)  # 3 alheios + coord_stop.py, nao 3+2


# ---------------------------------------------------------------------------
# Backup criado ANTES da escrita (regra 3)
# ---------------------------------------------------------------------------


class TestBackup(_AmbienteTemporario):
    def test_backup_e_criado_com_o_conteudo_original_antes_da_escrita(self):
        "backup datado de settings.json e criado com o conteudo ORIGINAL antes de qualquer merge ser gravado"
        original = _settings_real_fixture()
        self._escrever_settings(original)

        agora = datetime(2026, 9, 11, 15, 30, 45)
        relatorio = install.instalar(self.destino, self.repo, dry_run=False, _agora=agora)

        self.assertTrue(relatorio.ok, relatorio.erro)
        self.assertIsNotNone(relatorio.backup_path)
        self.assertTrue(os.path.isfile(relatorio.backup_path))
        self.assertIn("settings.json.bak-20260911-153045", relatorio.backup_path)

        with open(relatorio.backup_path, "r", encoding="utf-8") as fh:
            backup_dados = json.load(fh)
        self.assertEqual(backup_dados, original, "backup tem que conter o settings.json ORIGINAL, sem o merge")

        pos = self._ler_settings()
        self.assertNotEqual(pos, original, "settings.json deveria ter sido alterado pelo merge")

    def test_backup_falho_aborta_sem_escrever_settings(self):
        "se a criacao do backup falhar, instalar() aborta e settings.json permanece intocado"
        from unittest import mock

        original = _settings_real_fixture()
        self._escrever_settings(original)

        antes = self._ler_settings()
        with mock.patch.object(install, "_fazer_backup", side_effect=OSError("disco cheio (simulado)")):
            relatorio = install.instalar(self.destino, self.repo, dry_run=False)

        self.assertFalse(relatorio.ok)
        self.assertIsNotNone(relatorio.erro)
        depois = self._ler_settings()
        self.assertEqual(antes, depois, "settings.json foi alterado mesmo com o backup tendo falhado")
        # nada foi copiado tambem
        self.assertFalse(os.path.isdir(os.path.join(self.destino, "hooks")))

    def test_duas_instalacoes_no_mesmo_segundo_geram_backups_distintos(self):
        "duas instalacoes com o mesmo timestamp (mesmo segundo) nao colidem no nome do backup"
        original = _settings_real_fixture()
        self._escrever_settings(original)
        agora = datetime(2026, 9, 11, 19, 45, 17)

        r1 = install.instalar(self.destino, self.repo, dry_run=False, _agora=agora)
        self.assertTrue(r1.ok, r1.erro)
        r2 = install.instalar(self.destino, self.repo, dry_run=False, _agora=agora)
        self.assertTrue(r2.ok, r2.erro)

        self.assertNotEqual(r1.backup_path, r2.backup_path)
        self.assertTrue(os.path.isfile(r1.backup_path))
        self.assertTrue(os.path.isfile(r2.backup_path))


# ---------------------------------------------------------------------------
# JSON invalido aborta sem tocar no arquivo (regra 2)
# ---------------------------------------------------------------------------


class TestJsonInvalidoAborta(_AmbienteTemporario):
    def test_settings_json_invalido_aborta_sem_tocar_no_arquivo(self):
        "settings.json com JSON invalido faz instalar() abortar sem alterar o arquivo nem criar backup"
        caminho = os.path.join(self.destino, "settings.json")
        texto_quebrado = '{"hooks": {"Stop": [ISSO NAO E JSON VALIDO'
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(texto_quebrado)

        relatorio = install.instalar(self.destino, self.repo, dry_run=False)

        self.assertFalse(relatorio.ok)
        self.assertIsNotNone(relatorio.erro)

        with open(caminho, "r", encoding="utf-8") as fh:
            depois = fh.read()
        self.assertEqual(depois, texto_quebrado, "arquivo invalido foi tocado mesmo devendo abortar")

        # nenhum backup foi criado, nada foi copiado
        listagem = os.listdir(self.destino)
        self.assertEqual(listagem, ["settings.json"])

    def test_dry_run_tambem_aborta_em_json_invalido(self):
        "dry-run tambem detecta JSON invalido e aborta (nao finge que daria certo)"
        caminho = os.path.join(self.destino, "settings.json")
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write("{ nao fecha")

        relatorio = install.instalar(self.destino, self.repo, dry_run=True)
        self.assertFalse(relatorio.ok)
        self.assertIsNotNone(relatorio.erro)


# ---------------------------------------------------------------------------
# desinstalar() remove so o nosso (regra 6)
# ---------------------------------------------------------------------------


class TestDesinstalar(_AmbienteTemporario):
    def test_desinstalar_remove_so_os_hooks_do_cc_coord(self):
        "desinstalar remove os 9 hooks do cc-coord e deixa os hooks de terceiros, no mesmo evento/matcher, intactos"
        original = _settings_real_fixture()
        self._escrever_settings(original)
        r_install = install.instalar(self.destino, self.repo, dry_run=False)
        self.assertTrue(r_install.ok, r_install.erro)

        r_uninstall = install.desinstalar(self.destino)
        self.assertTrue(r_uninstall.ok, r_uninstall.erro)
        self.assertEqual(len(r_uninstall.hooks_removidos), len(install.HOOKS_SPECS))

        pos = self._ler_settings()

        # hooks de terceiros continuam todos la
        terceiros_esperados = [
            "block_env_edit.py",
            "pre_push_migration_gate.py",
            "context_alert.py",
            "verify_gate.py",
            "obsidian_stop.py",
            "memory_recall_start.py",
            "hook.mjs",
        ]
        texto_pos = json.dumps(pos)
        for nome in terceiros_esperados:
            self.assertIn(nome, texto_pos, f"desinstalar removeu hook de terceiro {nome}")

        # nenhum dos 8 scripts do cc-coord sobrou no settings.json
        for spec in install.HOOKS_SPECS:
            self.assertEqual(self._contar_nossos(pos, spec["script"]), 0, f"{spec['script']} nao foi removido")

        # o grupo PreToolUse/Bash volta a ter so 1 hook (o alheio)
        grupo_bash = next(g for g in pos["hooks"]["PreToolUse"] if g["matcher"] == "Bash")
        self.assertEqual(len(grupo_bash["hooks"]), 1)
        self.assertIn("pre_push_migration_gate.py", grupo_bash["hooks"][0]["command"])

        # o grupo PreToolUse/Edit|Write|NotebookEdit (so nosso) some inteiro
        matchers_pre = [g["matcher"] for g in pos["hooks"]["PreToolUse"]]
        self.assertNotIn("Edit|Write|NotebookEdit", matchers_pre)

        # eventos que so existiam por nossa causa (PostToolBatch, FileChanged,
        # SessionEnd) somem de vez, ja que ficaram vazios
        self.assertNotIn("PostToolBatch", pos["hooks"])
        self.assertNotIn("FileChanged", pos["hooks"])
        self.assertNotIn("SessionEnd", pos["hooks"])

        # CCOORD_SRC removido, resto do env preservado
        self.assertNotIn("CCOORD_SRC", pos.get("env", {}))
        self.assertEqual(pos["env"]["DO_NOT_TRACK"], "1")

        # entrypoints e regra removidos do disco
        for spec in install.HOOKS_SPECS:
            self.assertFalse(os.path.isfile(os.path.join(self.destino, "hooks", spec["script"])))
        self.assertFalse(os.path.isfile(os.path.join(self.destino, "rules", install.RULE_FILENAME)))

    def test_desinstalar_sem_settings_json_nao_quebra(self):
        "desinstalar sem settings.json (nunca instalado, ou ja removido por fora) nao levanta excecao"
        relatorio = install.desinstalar(self.destino)
        self.assertTrue(relatorio.ok)
        self.assertEqual(relatorio.hooks_removidos, [])

    def test_desinstalar_falha_na_escrita_devolve_relatorio_e_nao_remove_entrypoints(self):
        """achado ALTA (11/09): a escrita final do settings.json em
        desinstalar() nao tinha try/except -- settings.json marcado
        read-only por fora (ou disco cheio, ACL negada) fazia a excecao subir
        CRUA ate o chamador, mesmo com o backup ja tendo sido criado com
        sucesso (a docstring do modulo promete Relatorio sempre, inclusive
        em falha). Alem de nao propagar, a limpeza dos entrypoints/regra tem
        de ser pulada: se settings.json nao foi atualizado, ele ainda
        referencia os 8 scripts -- apaga-los criaria o mesmo settings.json
        apontando para hook inexistente do achado 2, so que pelo caminho do
        uninstall."""
        from unittest import mock

        original = _settings_real_fixture()
        self._escrever_settings(original)
        r_install = install.instalar(self.destino, self.repo, dry_run=False)
        self.assertTrue(r_install.ok, r_install.erro)
        caminho_entrypoint = os.path.join(self.destino, "hooks", "coord_stop.py")
        self.assertTrue(os.path.isfile(caminho_entrypoint))

        with mock.patch.object(install, "_escrever_texto", side_effect=OSError("permissao negada (simulado)")):
            relatorio = install.desinstalar(self.destino)

        self.assertFalse(relatorio.ok, "desinstalar() deveria devolver ok=False, nao propagar excecao")
        self.assertIsNotNone(relatorio.erro)
        self.assertTrue(
            os.path.isfile(caminho_entrypoint),
            "entrypoint foi removido mesmo com a escrita do settings.json tendo falhado -- "
            "settings.json ainda referencia um script que sumiu do disco",
        )
        # settings.json em si nao foi tocado (o mock impediu a escrita real)
        pos = self._ler_settings()
        self.assertIn("coord_stop.py", json.dumps(pos), "settings.json nao deveria ter mudado")


# ---------------------------------------------------------------------------
# --dry-run nao escreve nada
# ---------------------------------------------------------------------------


class TestDryRun(_AmbienteTemporario):
    def test_dry_run_nao_altera_settings_nem_cria_backup_ou_copia_arquivos(self):
        "@spec:AC-012 --dry-run relata os 9 hooks que entrariam mas nao escreve nada em disco"
        original = _settings_real_fixture()
        caminho_settings = self._escrever_settings(original)
        mtime_antes = os.path.getmtime(caminho_settings)
        with open(caminho_settings, "r", encoding="utf-8") as fh:
            conteudo_antes = fh.read()

        relatorio = install.instalar(self.destino, self.repo, dry_run=True)

        self.assertTrue(relatorio.ok, relatorio.erro)
        self.assertTrue(relatorio.dry_run)
        self.assertEqual(len(relatorio.hooks_adicionados), len(install.HOOKS_SPECS))

        # nada mudou no disco
        with open(caminho_settings, "r", encoding="utf-8") as fh:
            conteudo_depois = fh.read()
        self.assertEqual(conteudo_antes, conteudo_depois)
        self.assertEqual(mtime_antes, os.path.getmtime(caminho_settings))

        # nenhum backup, nenhum hooks/ ou rules/ criado no destino
        self.assertEqual(sorted(os.listdir(self.destino)), ["settings.json"])

        # a saida textual e legivel e cita os 9 hooks, os eventos e o backup previsto
        texto = relatorio.to_text()
        self.assertIn("DRY-RUN", texto)
        self.assertIn("coord_session_start.py", texto)
        self.assertIn("coord_pre_write.py", texto)
        self.assertIn("coord_pre_bash.py", texto)
        self.assertIn("coord_post_batch.py", texto)
        self.assertIn("coord_file_changed.py", texto)
        self.assertIn("coord_stop.py", texto)
        self.assertIn("coord_session_end.py", texto)
        self.assertIn("SessionStart", texto)
        self.assertIn("PreToolUse", texto)
        self.assertIn("Backup previsto", texto)
        self.assertIn("settings.json.bak-", texto)
        self.assertIn("Nenhuma escrita foi feita", texto)

    def test_dry_run_em_settings_inexistente_nao_cria_nada(self):
        "dry-run com settings.json ainda inexistente relata 'sera criado' e continua sem escrever nada"
        relatorio = install.instalar(self.destino, self.repo, dry_run=True)
        self.assertTrue(relatorio.ok, relatorio.erro)
        self.assertFalse(relatorio.settings_existia)
        self.assertEqual(os.listdir(self.destino), [])  # nada foi criado no destino
        self.assertIn("nao existia", relatorio.to_text())


# ---------------------------------------------------------------------------
# CLI (`ccoord install` / `ccoord uninstall` / `--dry-run`) -- sempre com
# --destino/--src-repo explicitos, NUNCA o default (~/.claude real).
# ---------------------------------------------------------------------------


class TestInterpretadorAbsoluto(_AmbienteTemporario):
    def test_comando_gravado_usa_sys_executable_no_lugar_de_python_generico(self):
        """achado ALTA (11/09): gravar so 'python' confia no PATH resolver no
        momento em que o HARNESS invoca o hook via bash -c, nao no momento
        deste instalar() -- trocar/reinstalar o Python ou uma distribuicao
        (Anaconda) empurrando seu proprio 'python' na frente do PATH quebra
        os 9 hooks ao mesmo tempo, com o kill de peer saindo liberado mesmo
        assim (falha no bash, antes do try/except do proprio entrypoint)."""
        self._escrever_settings(_settings_real_fixture())
        relatorio = install.instalar(self.destino, self.repo, dry_run=False)
        self.assertTrue(relatorio.ok, relatorio.erro)

        pos = self._ler_settings()
        grupo_bash = next(g for g in pos["hooks"]["PreToolUse"] if g["matcher"] == "Bash")
        comando_nosso = next(
            h["command"] for h in grupo_bash["hooks"] if "coord_pre_bash.py" in h["command"]
        )
        self.assertIn(
            sys.executable.replace("\\", "/"), comando_nosso,
            f"comando gravado nao usa o interpretador absoluto: {comando_nosso!r}",
        )
        self.assertFalse(
            comando_nosso.startswith("python "),
            f"comando gravado ainda usa o nome generico 'python' em vez do caminho absoluto: {comando_nosso!r}",
        )


class TestFormatacaoPreservada(_AmbienteTemporario):
    def test_roundtrip_preserva_indent_eol_e_newline_final_do_original(self):
        """achado MEDIA (11/09): sem detectar a formatacao original, um
        roundtrip instalar()+desinstalar() volta ao mesmo CONTEUDO logico mas
        reformata o arquivo INTEIRO (indent, CRLF->LF, perde newline final) --
        isso esconde qualquer diff real dentro do ruido de quem versiona ou
        compara settings.json antes/depois."""
        caminho = os.path.join(self.destino, "settings.json")
        original_dict = _settings_real_fixture()
        texto_original = json.dumps(original_dict, indent=4, ensure_ascii=False)
        texto_original = texto_original.replace("\n", "\r\n") + "\r\n"
        with open(caminho, "wb") as fh:
            fh.write(texto_original.encode("utf-8"))

        r1 = install.instalar(self.destino, self.repo, dry_run=False)
        self.assertTrue(r1.ok, r1.erro)
        r2 = install.desinstalar(self.destino)
        self.assertTrue(r2.ok, r2.erro)

        with open(caminho, "rb") as fh:
            texto_final = fh.read().decode("utf-8")

        self.assertEqual(json.loads(texto_final), original_dict)
        self.assertTrue(
            texto_final.startswith("{\r\n    "),
            f"nao preservou indent=4 + CRLF do original: {texto_final[:40]!r}",
        )
        self.assertTrue(texto_final.endswith("\r\n"), "nao preservou o newline final do original")

    def test_settings_novo_continua_usando_indent_2_lf_sem_newline_final(self):
        "settings.json criado do zero (sem original a preservar) mantem o default de sempre"
        relatorio = install.instalar(self.destino, self.repo, dry_run=False)
        self.assertTrue(relatorio.ok, relatorio.erro)

        caminho = os.path.join(self.destino, "settings.json")
        with open(caminho, "rb") as fh:
            bruto = fh.read()
        self.assertNotIn(b"\r\n", bruto, "arquivo novo nao deveria usar CRLF")
        self.assertTrue(bruto.startswith(b'{\n  "'), f"arquivo novo deveria usar indent=2: {bruto[:20]!r}")
        self.assertFalse(bruto.endswith(b"\n"), "arquivo novo nao deveria ganhar newline final por padrao")


class TestBackupRotacaoERestore(_AmbienteTemporario):
    def test_backups_antigos_sao_rotacionados_alem_do_limite(self):
        """achado MEDIA (11/09): sem rotacao/limite, o backup acumula para
        sempre -- 5 ciclos de instalar+desinstalar ja produziam 10 arquivos
        settings.json.bak-*, sem fim a vista."""
        self._escrever_settings(_settings_real_fixture())

        total_ciclos = install.MAX_BACKUPS + 3
        for i in range(total_ciclos):
            agora = datetime(2026, 9, 12, 10, 0, i)
            r1 = install.instalar(self.destino, self.repo, dry_run=False, _agora=agora)
            self.assertTrue(r1.ok, r1.erro)
            r2 = install.desinstalar(self.destino, _agora=agora)
            self.assertTrue(r2.ok, r2.erro)

        backups = [f for f in os.listdir(self.destino) if f.startswith("settings.json.bak-")]
        self.assertLessEqual(
            len(backups), install.MAX_BACKUPS,
            f"backups nao foram rotacionados: {len(backups)} arquivos (limite {install.MAX_BACKUPS})",
        )

    def test_restaurar_sem_caminho_usa_o_backup_mais_recente(self):
        """achado MEDIA (11/09): o modulo so escrevia backup, nunca lia um de
        volta -- 'restaurar de verdade' dependia de copiar o .bak-* a mao."""
        original = _settings_real_fixture()
        self._escrever_settings(original)
        r1 = install.instalar(self.destino, self.repo, dry_run=False)
        self.assertTrue(r1.ok, r1.erro)

        pos_install = self._ler_settings()
        self.assertIn("CCOORD_SRC", pos_install.get("env", {}))

        r_restore = install.restaurar(self.destino)
        self.assertTrue(r_restore.ok, r_restore.erro)

        pos_restore = self._ler_settings()
        self.assertEqual(pos_restore, original, "restaurar() nao devolveu settings.json ao estado do backup")

    def test_restaurar_sem_nenhum_backup_aborta_com_relatorio(self):
        "restaurar() sem nenhum backup no destino devolve Relatorio(ok=False), nao estoura"
        relatorio = install.restaurar(self.destino)
        self.assertFalse(relatorio.ok)
        self.assertIsNotNone(relatorio.erro)


class TestCli(_AmbienteTemporario):
    def _run_cli(self, argv, capsys_buf):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            codigo = install.main(argv)
        return codigo, buf.getvalue()

    def test_cli_install_dry_run_com_destino_explicito(self):
        "ccoord install --dry-run --destino <tmp> --src-repo <tmp> nunca toca no default"
        self._escrever_settings(_settings_real_fixture())
        codigo, saida = self._run_cli(
            ["install", "--dry-run", "--destino", self.destino, "--src-repo", self.repo], None
        )
        self.assertEqual(codigo, 0)
        self.assertIn("DRY-RUN", saida)
        self.assertIn("coord_session_start.py", saida)

    def test_cli_install_depois_uninstall_via_argv(self):
        "ccoord install seguido de ccoord uninstall via CLI, ambos com destino explicito"
        self._escrever_settings(_settings_real_fixture())

        codigo1, saida1 = self._run_cli(
            ["install", "--destino", self.destino, "--src-repo", self.repo], None
        )
        self.assertEqual(codigo1, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.destino, "hooks", "coord_stop.py")))

        codigo2, saida2 = self._run_cli(["uninstall", "--destino", self.destino], None)
        self.assertEqual(codigo2, 0)
        self.assertFalse(os.path.isfile(os.path.join(self.destino, "hooks", "coord_stop.py")))


if __name__ == "__main__":
    unittest.main()


class TestSettingsComFormaInesperada(unittest.TestCase):
    """Achado da auditoria adversarial (11/09): `hooks` como LISTA estourava
    AttributeError cru, inclusive em --dry-run. Sem perda de dado (o crash vem
    antes de qualquer escrita), mas --dry-run tem de ser SEMPRE seguro e o
    contrato e devolver Relatorio(ok=False) com motivo legivel.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ccoord_install_forma_")
        self.destino = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _escrever(self, conteudo: str) -> str:
        caminho = os.path.join(self.destino, "settings.json")
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(conteudo)
        return caminho

    def test_hooks_como_lista_aborta_sem_crash_e_sem_escrever(self):
        "settings.json com hooks em formato inesperado aborta com motivo, sem tocar no arquivo"
        caminho = self._escrever('{"hooks": ["deveria ser um objeto"]}')
        antes = open(caminho, "rb").read()

        rel = install.instalar(self.destino, str(SRC.parent), dry_run=True)

        self.assertFalse(rel.ok, "deveria abortar, nao seguir")
        self.assertIn("hooks", (rel.erro or "").lower())
        self.assertEqual(open(caminho, "rb").read(), antes, "o arquivo nao pode ter sido tocado")

    def test_hooks_como_string_tambem_aborta(self):
        "outra forma inesperada (string) tambem aborta em vez de estourar"
        self._escrever('{"hooks": "nada disso"}')
        rel = install.instalar(self.destino, str(SRC.parent), dry_run=True)
        self.assertFalse(rel.ok)

    def test_formas_aninhadas_malformadas_abortam_sem_estourar(self):
        "malformacao ANINHADA (evento/grupo/entradas) aborta com motivo, sem AttributeError cru"
        casos = {
            "evento como string": '{"hooks": {"Stop": "nao-lista"}}',
            "evento como dict": '{"hooks": {"Stop": {"a": 1}}}',
            "grupo como lista": '{"hooks": {"Stop": [[1, 2]]}}',
            "grupo como string": '{"hooks": {"Stop": ["x"]}}',
            "entradas como dict": '{"hooks": {"Stop": [{"hooks": {"a": 1}}]}}',
        }
        for nome, conteudo in casos.items():
            with self.subTest(forma=nome):
                caminho = self._escrever(conteudo)
                antes = open(caminho, "rb").read()
                rel = install.instalar(self.destino, str(SRC.parent), dry_run=True)
                self.assertFalse(rel.ok, f"{nome}: deveria abortar")
                self.assertEqual(open(caminho, "rb").read(), antes, f"{nome}: arquivo foi tocado")

    def test_hooks_null_explicito_nao_estoura(self):
        '"hooks": null equivale a nao ter hooks: instala normalmente, sem crash'
        self._escrever('{"hooks": null}')
        rel = install.instalar(self.destino, str(SRC.parent), dry_run=True)
        self.assertTrue(rel.ok, f"deveria seguir normalmente, veio: {rel.erro!r}")
