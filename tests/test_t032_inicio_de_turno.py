"""T-032 — o claim do turno anterior morre quando o proximo prompt chega.

  AC-024 — claim de turno nao sobrevive ao prompt seguinte.
  AC-025 — o release do inicio do turno nao alcanca subagente, outra sessao,
           nem claim de escopo `session`.
  AC-026 — o hook nunca bloqueia nem fala.
  AC-027 — prompt que nao veio do usuario nao encerra turno nenhum.

Medicao que originou esta task (`.specs/.../medicao-fronteira-de-turno.md`):
cruzando o `events.log` com os transcripts JSONL — a unica fonte que tem
fronteira de turno —, **20,1% dos claims de turno atravessam pelo menos um
prompt do usuario** (678 de 3.368), e 10 disputas em 5 dias foram contra dono
de turno ja encerrado.

Nota de metodo, herdada da T-023: o par de testes de cada regra so protege se
tiver o lado de NAO-VACUIDADE — o caso que TEM de sobrar, ou que TEM de sair.
Um teste que so confere "o claim sumiu" passa igual com o hook apagando tudo
que ve, que e justamente o defeito a evitar aqui.
"""

from __future__ import annotations

try:
    from . import _guarda  # noqa: F401
except ImportError:  # carregado solto
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

HOOK = "coord_user_prompt.py"
SESSAO = "sessao-dona"


def _owner(session_id=SESSAO, agent_id=None, pid=None):
    # `proc_start=""`: sem tick armazenado o dono e lido como VIVO (fail-safe).
    # Um tick inventado faria o claim passar por morto e sumir por outro
    # caminho que nao o testado — armadilha ja paga na T-025.
    return claims.Owner(
        session_id=session_id,
        pid=pid if pid is not None else os.getpid(),
        proc_start="",
        name=session_id,
        agent_id=agent_id,
    )


def _claim(resource, owner, *, scope="turn", faixa=(10, 12)):
    r = claims.claim(
        resource,
        owner,
        ttl_s=900,
        meta={"path": resource, "range": faixa, "scope": scope},
        esta_vivo=lambda o: True,
    )
    assert r.ok, f"fixture nao conseguiu criar o claim {resource}: {r.reason}"
    return r


def _existe(resource) -> bool:
    return claims.owner_of(resource, esta_vivo=lambda o: True) is not None


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccoord_t032_")
        self.home = os.path.join(self.tmp, "coord")
        self.sessions = os.path.join(self.tmp, "sessions")
        os.makedirs(self.home, exist_ok=True)
        os.makedirs(self.sessions, exist_ok=True)
        self._antigos = {k: os.environ.get(k) for k in ("CCOORD_HOME", "CCOORD_SESSIONS_DIR")}
        os.environ["CCOORD_HOME"] = self.home
        os.environ["CCOORD_SESSIONS_DIR"] = self.sessions

    def tearDown(self):
        for k, v in self._antigos.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def rodar(self, payload=None, *, com_pythonioencoding=True):
        env = dict(os.environ)
        if com_pythonioencoding:
            env["PYTHONIOENCODING"] = "utf-8"
        else:
            env.pop("PYTHONIOENCODING", None)
        env["CCOORD_HOME"] = self.home
        env["CCOORD_SESSIONS_DIR"] = self.sessions
        env["CCOORD_SRC"] = str(SRC)
        # `CLAUDE_CODE_SESSION_ID` fora do ambiente: `identidade()` usa o env
        # como fallback do `session_id`, e deixa-lo vazar do ambiente real
        # faria o teste de dono indeterminavel (AC-026) passar por engano.
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        entrada = "" if payload is None else json.dumps(payload)
        proc = subprocess.run(
            [PYTHON, str(HOOKS / HOOK)],
            input=entrada,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )
        return proc

    def payload(self, **extra):
        base = {
            "session_id": SESSAO,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "proximo pedido do Vinicius",
            "cwd": self.tmp,
        }
        base.update(extra)
        return base


class TestAC024LiberaOTurnoAnterior(_Base):
    def test_claim_de_turno_sai_no_prompt_seguinte(self):
        """@spec:AC-024 claim de turno do main e liberado quando o proximo prompt chega"""
        _claim("file:alvo.md", _owner())
        self.assertTrue(_existe("file:alvo.md"), "fixture nao criou o claim")

        proc = self.rodar(self.payload())

        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        self.assertFalse(
            _existe("file:alvo.md"),
            "o claim do turno anterior sobreviveu ao prompt seguinte — "
            "e o defeito que esta task existe para fechar",
        )

    def test_nao_vacuidade_sem_o_hook_o_claim_fica(self):
        """@spec:AC-024 controle: o claim so some por causa do hook, nao sozinho"""
        _claim("file:alvo.md", _owner())
        # Nenhuma chamada ao hook aqui, de proposito.
        self.assertTrue(
            _existe("file:alvo.md"),
            "o claim sumiu sem o hook rodar — o teste acima nao provaria nada",
        )


class TestAC025NaoAlcancaQuemNaoEMeu(_Base):
    def test_poupa_subagente_outra_sessao_e_escopo_sessao(self):
        """@spec:AC-025 subagente, outra sessao e claim de sessao sobrevivem"""
        _claim("file:do-main.md", _owner())
        _claim("file:do-subagente.md", _owner(agent_id="agente-7"))
        _claim("file:de-outra-sessao.md", _owner(session_id="sessao-peer"))
        _claim("browser:playwright", _owner(), scope="session", faixa=None)

        proc = self.rodar(self.payload())
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])

        self.assertFalse(_existe("file:do-main.md"), "o claim do main tinha de sair")
        self.assertTrue(
            _existe("file:do-subagente.md"),
            "apagou o claim de um SUBAGENTE: se ele for de background e ainda "
            "estiver escrevendo, a peer que editar ali ouve 'sem sobreposicao' "
            "— silencio indevido, o pior modo de falha desta feature",
        )
        self.assertTrue(_existe("file:de-outra-sessao.md"), "apagou claim de outra sessao")
        self.assertTrue(
            _existe("browser:playwright"),
            "apagou claim de escopo `session`: o perfil do browser atravessa "
            "turnos de proposito (T-022)",
        )


class TestAC027OrigemQueNaoEncerraTurno(_Base):
    def test_poll_event_nao_libera(self):
        """@spec:AC-027 origem `poll_event` (dispara no enqueue) nao encerra turno"""
        _claim("file:alvo.md", _owner())
        proc = self.rodar(self.payload(source="poll_event"))
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        self.assertTrue(
            _existe("file:alvo.md"),
            "liberou num `poll_event`, que o binario documenta como disparado "
            "NO ENQUEUE — ou seja, possivelmente com o turno ainda em andamento",
        )

    def test_nao_vacuidade_origem_do_usuario_libera(self):
        """@spec:AC-027 controle: com origem do composer o release acontece"""
        _claim("file:alvo.md", _owner())
        proc = self.rodar(self.payload(source="user"))
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        self.assertFalse(
            _existe("file:alvo.md"),
            "nao liberou com `source=user` — o filtro do AC-027 esta largo "
            "demais e o hook virou inerte",
        )

    def test_origem_system_libera(self):
        """@spec:AC-027 origem `system` (mensagem de peer) libera: vira turno proprio"""
        # Este teste existe porque o AC-027 dizia o CONTRARIO do codigo ate a
        # auditoria de 17/09 pegar (achado ALTA-2): a primeira versao da spec
        # foi escrita antes da medicao, listando `system` como "nao encerra
        # turno". Medido depois no transcript: mensagem de peer e notificacao
        # de tarefa geram `promptId` novo e `Stop` proprio, em SEQUENCIA -- ou
        # seja, quando uma delas chega o turno anterior ja acabou de verdade.
        # O texto do AC foi corrigido; este teste trava a regra medida.
        _claim("file:alvo.md", _owner())
        proc = self.rodar(self.payload(source="system"))
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        self.assertFalse(
            _existe("file:alvo.md"),
            "nao liberou com `source=system` -- o filtro esta largo demais e "
            "mensagem de peer deixaria o claim do turno anterior vazando",
        )

    def test_campo_ausente_libera(self):
        """@spec:AC-027 sem o campo `source` (o estado deste build) o hook funciona"""
        # Medido em 17/09: este build NAO envia `source`. Se o filtro exigisse
        # `== "user"`, o hook estaria instalado e sem efeito nenhum.
        _claim("file:alvo.md", _owner())
        proc = self.rodar(self.payload())
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        self.assertFalse(_existe("file:alvo.md"), "o hook ficou inerte sem o campo `source`")


class TestAC026NuncaBloqueiaNemFala(_Base):
    def test_payload_vazio_lixo_e_sem_sessao(self):
        """@spec:AC-026 payload vazio, lixo ou sem session_id: exit 0 e stdout vazio"""
        for rotulo, entrada in (
            ("vazio", None),
            ("sem session_id", {"hook_event_name": "UserPromptSubmit", "prompt": "x"}),
            ("session_id vazio", {"session_id": "", "hook_event_name": "UserPromptSubmit"}),
        ):
            with self.subTest(rotulo):
                proc = self.rodar(entrada)
                self.assertEqual(proc.returncode, 0, f"{rotulo}: {proc.stderr[:300]}")
                self.assertEqual(
                    proc.stdout,
                    "",
                    f"{rotulo}: stdout nao vazio vira `additionalContext` "
                    "AUTOMATICO neste evento — token gasto em toda mensagem",
                )

    def test_lixo_que_nao_e_json(self):
        """@spec:AC-026 stdin que nao e JSON nao derruba o prompt"""
        env = dict(os.environ)
        env["CCOORD_HOME"] = self.home
        env["CCOORD_SESSIONS_DIR"] = self.sessions
        env["CCOORD_SRC"] = str(SRC)
        proc = subprocess.run(
            [PYTHON, str(HOOKS / HOOK)],
            input="isto nao e json {{{",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        self.assertEqual(proc.stdout, "")

    def test_dono_vazio_nao_come_claim_alheio(self):
        """@spec:AC-026 dono indeterminavel nao libera claim de ninguem"""
        # Claim gravado com `session_id` vazio existe no mundo real (payload
        # incompleto). Sem a guarda, o hook de QUALQUER sessao o apagaria.
        _claim("file:orfao.md", _owner(session_id=""))
        _claim("file:do-main.md", _owner())

        proc = self.rodar({"hook_event_name": "UserPromptSubmit", "prompt": "x"})

        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        self.assertTrue(_existe("file:orfao.md"), "dono vazio casou com claim de dono vazio")
        self.assertTrue(_existe("file:do-main.md"), "apagou claim de dono identificado")

    def test_sem_pythonioencoding_tambem_sai_calado(self):
        """@spec:AC-026 stdout vazio tambem no ambiente REAL do harness (sem PYTHONIOENCODING)"""
        # O harness nao seta PYTHONIOENCODING (T-035). Rodar so com a variavel
        # setada mede uma condicao que nao e a de producao.
        _claim("file:alvo.md", _owner())
        proc = self.rodar(self.payload(), com_pythonioencoding=False)
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        self.assertEqual(proc.stdout, "")
        self.assertFalse(_existe("file:alvo.md"))


class TestAchadosDaAuditoria(_Base):
    """A3 e A4 da auditoria adversarial de 17/09 sobre este mesmo hook."""

    def test_agent_id_vazio_nao_deixa_o_hook_inerte(self):
        """@spec:AC-024 `agent_id` vazio no payload nao impede o release (A3)"""
        # `""` e `None` sao ambos "sem agente", mas so `None` casa na comparacao
        # de `agente_exato`. Sem a normalizacao em `hookio.identidade`, um
        # payload com `agent_id: ""` deixaria o hook INERTE e MUDO: sem apagar
        # nada e sem dizer por que.
        _claim("file:alvo.md", _owner())
        proc = self.rodar(self.payload(agent_id=""))
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        self.assertFalse(
            _existe("file:alvo.md"),
            "`agent_id: \"\"` deixou o hook inerte -- o dono do claim tem "
            "agent_id None e o pedinte ficou com \"\", que nao casa",
        )

    def test_nao_vacuidade_agent_id_real_continua_poupando_subagente(self):
        """@spec:AC-025 controle: `agent_id` de verdade continua sendo respeitado"""
        # Contraprova da normalizacao acima: ela nao pode ter transformado
        # "agente de verdade" em "sem agente".
        #
        # Este par de assercoes foi REESCRITO depois que a 3a auditoria (17/09)
        # mediu que a versao anterior tinha poder discriminante ZERO: com o
        # mutante `agent_id = None` sempre (que colapsa TODO agente), o teste
        # continuava verde -- o `agente_exato` salvava a assercao pelo motivo
        # errado, porque dono e pedinte viravam ambos None e o claim era
        # removido... nao, era preservado, e a assercao passava sem provar
        # nada sobre a identidade. Agora o teste exerce as DUAS direcoes na
        # mesma execucao, e e a segunda que mata o mutante: se o agent_id
        # colapsar, o claim do proprio agente deixa de ser reconhecido como
        # dele e sobrevive quando deveria sair.
        _claim("file:do-agente-7.md", _owner(agent_id="agente-7"))
        _claim("file:do-agente-9.md", _owner(agent_id="agente-9"))

        proc = self.rodar(self.payload(agent_id="agente-9"))
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])

        self.assertTrue(
            _existe("file:do-agente-7.md"),
            "a normalizacao de agent_id colapsou agentes distintos: o claim de "
            "OUTRO subagente foi liberado",
        )
        self.assertFalse(
            _existe("file:do-agente-9.md"),
            "o claim do PROPRIO agente que enviou o prompt sobreviveu -- sinal "
            "de que o agent_id nao esta chegando na identidade (colapsado para "
            "None, ou perdido no caminho)",
        )

    def test_falha_de_import_deixa_rastro_no_events_log(self):
        """@spec:AC-026 fail-open registra o erro em vez de sumir calado (A4)"""
        # Cenario provado pela auditoria: `CCOORD_SRC` apontando para lugar
        # inexistente -> `import ccoord` falha. Antes: rc 0, stdout vazio,
        # claim intacto e NENHUMA linha em lugar nenhum -- indistinguivel de
        # "rodou e nao havia o que fazer".
        _claim("file:alvo.md", _owner())

        env = dict(os.environ)
        env["CCOORD_HOME"] = self.home
        env["CCOORD_SESSIONS_DIR"] = self.sessions
        env["CCOORD_SRC"] = os.path.join(self.tmp, "nao-existe")
        env["CLAUDE_CODE_SESSION_ID"] = "sessao-que-quebrou"
        # Sem o fallback relativo ao proprio arquivo, o hook copiado acharia o
        # `src/` do repo; copio o hook para fora da arvore para que o unico
        # caminho de import seja o CCOORD_SRC quebrado.
        import shutil

        hook_isolado = os.path.join(self.tmp, "coord_user_prompt.py")
        shutil.copy2(HOOKS / HOOK, hook_isolado)

        proc = subprocess.run(
            [PYTHON, hook_isolado],
            input=json.dumps(self.payload()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

        self.assertEqual(proc.returncode, 0, "fail-open quebrou o prompt do usuario")
        self.assertEqual(proc.stdout, "", "fail-open falou no stdout")
        self.assertTrue(_existe("file:alvo.md"), "liberou claim apesar de ter quebrado")

        log = os.path.join(self.home, "events.log")
        self.assertTrue(os.path.isfile(log), "nao registrou NADA no events.log")
        with open(log, "r", encoding="utf-8") as fh:
            linhas = [json.loads(l) for l in fh if l.strip()]
        erros = [l for l in linhas if l.get("origem") == "coord_user_prompt"]
        self.assertTrue(
            erros,
            f"nenhuma linha de erro deste hook no events.log: {linhas!r}",
        )
        self.assertEqual(erros[-1].get("event"), "error")
        # QUEM quebrou: sem o `session_id`, N linhas identicas nao distinguem
        # "uma sessao quebrada x 40 prompts" de "40 sessoes quebradas" -- e esta
        # maquina roda varias sessoes ao mesmo tempo, que e o problema inteiro
        # desta feature. Achado da 3a auditoria (17/09).
        self.assertEqual(
            erros[-1].get("session_id"),
            "sessao-que-quebrou",
            "a linha de erro nao diz QUAL sessao quebrou",
        )

    def _src_falso_que_levanta(self) -> str:
        """Monta um pacote `ccoord` falso cujo `release` LEVANTA.

        Injetar falha de verdade e o unico jeito de exercitar o `except` do
        hook a partir de um subprocesso -- `ler_payload()` nunca levanta (por
        desenho: devolve `{}` para qualquer lixo), entao nenhum payload hostil
        chega la dentro. Foi exatamente por isso que o mutante `return 2` no
        topo do `except` SOBREVIVEU aos 10 testes anteriores (achado ALTA-1 da
        auditoria de 17/09): o ramo existia, estava certo, e nada o cobria.

        O `_bootstrap_src_path` do hook aceita qualquer `CCOORD_SRC` que tenha
        um diretorio `ccoord` dentro -- e por essa porta que o falso entra.
        """
        src = os.path.join(self.tmp, "src_falso")
        pkg = os.path.join(src, "ccoord")
        os.makedirs(pkg, exist_ok=True)
        with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as fh:
            fh.write(
                "import json, sys\n"
                "class _Hookio:\n"
                "    @staticmethod\n"
                "    def ler_payload():\n"
                "        try:\n"
                "            return json.load(sys.stdin)\n"
                "        except Exception:\n"
                "            return {}\n"
                "    @staticmethod\n"
                "    def identidade(payload):\n"
                "        class O:\n"
                "            session_id = 'sessao-dona'\n"
                "            agent_id = None\n"
                "        return O()\n"
                "class _Claims:\n"
                "    @staticmethod\n"
                "    def release(*a, **k):\n"
                "        raise RuntimeError('falha injetada no release')\n"
                "hookio = _Hookio()\n"
                "claims = _Claims()\n"
            )
        return src

    def test_excecao_no_release_nao_bloqueia_o_prompt(self):
        """@spec:AC-026 se o `release` LEVANTAR, o hook ainda sai 0 e calado"""
        # Este e o cenario que de fato importa em producao: `claims.release`
        # varre diretorio e remove arquivo -- disco cheio, permissao negada e
        # estado corrompido levantam ali. Sair com codigo != 0 nao e detalhe:
        # **exit 2 BLOQUEIA o prompt do usuario** ("Prompt blocked: the
        # UserPromptSubmit hooks did not run over the submitted text").
        env = dict(os.environ)
        env["CCOORD_HOME"] = self.home
        env["CCOORD_SESSIONS_DIR"] = self.sessions
        env["CCOORD_SRC"] = self._src_falso_que_levanta()

        proc = subprocess.run(
            [PYTHON, str(HOOKS / HOOK)],
            input=json.dumps(self.payload()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

        self.assertEqual(
            proc.returncode,
            0,
            f"o hook saiu com {proc.returncode} -- qualquer codigo != 0 suja a "
            "tela do usuario, e 2 BLOQUEIA o prompt. stderr: {proc.stderr[:300]}",
        )
        self.assertEqual(proc.stdout, "", "falou no stdout ao falhar")

        # E a falha tem de deixar rastro (A4), senao vira silencio indistinguivel
        log = os.path.join(self.home, "events.log")
        self.assertTrue(os.path.isfile(log), "exceção no release nao registrou nada")
        with open(log, "r", encoding="utf-8") as fh:
            erros = [
                json.loads(l)
                for l in fh
                if l.strip() and json.loads(l).get("origem") == "coord_user_prompt"
            ]
        self.assertTrue(erros, "nenhuma linha de erro apesar da excecao")
        self.assertIn("falha injetada", erros[-1].get("erro", ""))

    def test_nao_vacuidade_o_src_falso_realmente_e_usado(self):
        """@spec:AC-026 controle: o pacote falso e MESMO importado pelo hook"""
        # Sem isto, o teste acima passaria mesmo que o `CCOORD_SRC` falso fosse
        # ignorado e o hook rodasse o caminho feliz com o pacote real -- exit 0
        # e stdout vazio sao o esperado NOS DOIS casos. O que distingue e o
        # claim: com o release levantando, ele TEM de sobreviver.
        _claim("file:alvo.md", _owner())
        env = dict(os.environ)
        env["CCOORD_HOME"] = self.home
        env["CCOORD_SESSIONS_DIR"] = self.sessions
        env["CCOORD_SRC"] = self._src_falso_que_levanta()

        proc = subprocess.run(
            [PYTHON, str(HOOKS / HOOK)],
            input=json.dumps(self.payload()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        self.assertTrue(
            _existe("file:alvo.md"),
            "o claim foi liberado -- ou seja, o pacote REAL rodou e o `src` "
            "falso foi ignorado; o teste de excecao nao prova nada",
        )

    def test_nao_vacuidade_caminho_feliz_nao_polui_o_log_de_erro(self):
        """@spec:AC-026 controle: execucao normal NAO grava linha de erro"""
        # Sem isto, o teste acima passaria com um hook que registra erro sempre
        # -- e o log de erro perderia todo o valor de sinal.
        _claim("file:alvo.md", _owner())
        proc = self.rodar(self.payload())
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])

        log = os.path.join(self.home, "events.log")
        erros = []
        if os.path.isfile(log):
            with open(log, "r", encoding="utf-8") as fh:
                for l in fh:
                    if not l.strip():
                        continue
                    d = json.loads(l)
                    if d.get("origem") == "coord_user_prompt":
                        erros.append(d)
        self.assertEqual(erros, [], "caminho feliz gravou erro no events.log")


if __name__ == "__main__":
    unittest.main()
