"""Testes de ccoord.hookio (unittest, stdlib - RNF-01, nada de pytest).

Os formatos verificados aqui sao os MEDIDOS em sessao real do CLI 2.1.261
(.specs/coordenacao-multissessao/medicao-hooks.md), nao a documentacao
publica dos hooks. Ler design.md secao 3.5 antes de mexer.
"""


from __future__ import annotations

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py):
# sem isto, rodar este arquivo sem CCOORD_HOME grava em ~/.claude/coord real.
try:
    from . import _guarda  # noqa: F401  (import por efeito colateral)
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401


import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(RAIZ), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ccoord import hookio  # noqa: E402
from ccoord.policy import Decision  # noqa: E402


def _capturar(func, *args, **kwargs):
    """Roda `func` capturando o stdout; devolve (retorno, stdout_como_texto)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        retorno = func(*args, **kwargs)
    return retorno, buf.getvalue()


def _unica_linha_json(saida: str) -> dict:
    linhas = [l for l in saida.splitlines() if l.strip()]
    assert len(linhas) == 1, f"esperava 1 linha de stdout, veio {len(linhas)}: {linhas!r}"
    return json.loads(linhas[0])


# ---------------------------------------------------------------------------
# ler_payload - tolerante a lixo/vazio/truncado/excecao no stream
# ---------------------------------------------------------------------------


class TestLerPayload(unittest.TestCase):
    def test_payload_valido_e_lido(self):
        "JSON valido e objeto e devolvido intacto como dict"
        dados = {"session_id": "s1", "hook_event_name": "PreToolUse"}
        resultado = hookio.ler_payload(io.StringIO(json.dumps(dados)))
        self.assertEqual(resultado, dados)

    def test_stream_vazio_nao_levanta(self):
        "stream vazio devolve dict vazio, nunca levanta excecao"
        self.assertEqual(hookio.ler_payload(io.StringIO("")), {})

    def test_so_espacos_em_branco_nao_levanta(self):
        "stream so com espacos/quebras de linha devolve dict vazio"
        self.assertEqual(hookio.ler_payload(io.StringIO("   \n\n  ")), {})

    def test_lixo_nao_json_nao_levanta(self):
        "texto que nao e JSON nenhum devolve dict vazio, nao propaga excecao"
        self.assertEqual(hookio.ler_payload(io.StringIO("isto nao e json {{{")), {})

    def test_json_truncado_nao_levanta(self):
        "JSON cortado no meio (payload truncado) devolve dict vazio"
        self.assertEqual(
            hookio.ler_payload(io.StringIO('{"session_id": "abc", "hook_ev')), {}
        )

    def test_json_valido_mas_nao_objeto_e_ignorado(self):
        "JSON valido porem lista (nao objeto) devolve dict vazio"
        self.assertEqual(hookio.ler_payload(io.StringIO("[1, 2, 3]")), {})

    def test_json_valido_string_solta_e_ignorado(self):
        "JSON valido porem string solta (nao objeto) devolve dict vazio"
        self.assertEqual(hookio.ler_payload(io.StringIO('"so uma string"')), {})

    def test_excecao_ao_ler_o_stream_nao_propaga(self):
        "excecao ao ler o stream (ex.: stdin fechado) nunca propaga"

        class _StreamQuebrado:
            def read(self):
                raise OSError("stdin fechado")

        self.assertEqual(hookio.ler_payload(_StreamQuebrado()), {})


# ---------------------------------------------------------------------------
# identidade - AC-009
# ---------------------------------------------------------------------------


class TestIdentidade(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_sessions_dir = os.environ.get("CCOORD_SESSIONS_DIR")
        self._old_pid = os.environ.get("CLAUDE_PID")
        self._old_session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CCOORD_SESSIONS_DIR"] = self._tmp.name
        os.environ.pop("CLAUDE_PID", None)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def tearDown(self):
        self._tmp.cleanup()
        for chave, valor in (
            ("CCOORD_SESSIONS_DIR", self._old_sessions_dir),
            ("CLAUDE_PID", self._old_pid),
            ("CLAUDE_CODE_SESSION_ID", self._old_session_id),
        ):
            if valor is None:
                os.environ.pop(chave, None)
            else:
                os.environ[chave] = valor

    def test_subagentes_irmaos_produzem_donos_diferentes(self):
        "@spec:AC-009 dois subagentes irmaos (mesmo session_id) tem donos diferentes"
        payload_sub1 = {
            "session_id": "sessao-pai",
            "agent_id": "sub-1",
            "hook_event_name": "PreToolUse",
        }
        payload_sub2 = {
            "session_id": "sessao-pai",
            "agent_id": "sub-2",
            "hook_event_name": "PreToolUse",
        }

        dono_sub1 = hookio.identidade(payload_sub1)
        dono_sub2 = hookio.identidade(payload_sub2)

        self.assertEqual(dono_sub1.session_id, dono_sub2.session_id)
        self.assertNotEqual(dono_sub1.agent_id, dono_sub2.agent_id)
        # o PONTO do AC-009: dois irmaos nao podem aparecer como o mesmo dono.
        # Owner (ccoord.claims) nao define __eq__ estrutural -- comparar os
        # objetos direto e SEMPRE != por identidade, mesmo com todos os
        # campos iguais, entao nao prova nada (achado auditoria hookio #1).
        # to_dict() e a comparacao estrutural que de fato depende do agent_id.
        self.assertNotEqual(dono_sub1.to_dict(), dono_sub2.to_dict())

    def test_sem_agent_id_a_identidade_e_a_da_sessao(self):
        "@spec:AC-009 sem agent_id no payload, a identidade e so a da sessao (pai)"
        payload_pai = {"session_id": "sessao-pai", "hook_event_name": "PreToolUse"}
        payload_sub = {
            "session_id": "sessao-pai",
            "agent_id": "sub-1",
            "hook_event_name": "PreToolUse",
        }

        dono_pai = hookio.identidade(payload_pai)
        dono_sub = hookio.identidade(payload_sub)

        self.assertIsNone(dono_pai.agent_id)
        self.assertEqual(dono_pai.session_id, "sessao-pai")
        # achado auditoria hookio #1: faltava checar dono_sub.agent_id -- sem
        # isso, uma regressao em identidade() que parasse de propagar o
        # agent_id do payload (colapsando o subagente na identidade do pai,
        # exatamente o que AC-009 existe para impedir) passava despercebida
        # por este teste, que so comparava dono_pai com dono_sub.
        self.assertEqual(dono_sub.agent_id, "sub-1")
        # a sessao-mae (sem agent_id) nao pode colidir com o subagente dela.
        # Owner nao define __eq__ estrutural -- comparar os objetos direto e
        # vacuo (sempre != por identidade); to_dict() e a checagem real.
        self.assertNotEqual(dono_pai.to_dict(), dono_sub.to_dict())

    def test_identidade_cai_para_session_id_do_env_sem_payload(self):
        "sem session_id no payload, identidade() cai para CLAUDE_CODE_SESSION_ID"
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sessao-do-env"
        dono = hookio.identidade({})
        self.assertEqual(dono.session_id, "sessao-do-env")

    def test_identidade_com_payload_none_nao_levanta(self):
        "identidade(None) e defensivo, nunca levanta"
        dono = hookio.identidade(None)
        self.assertEqual(dono.session_id, "")
        self.assertIsNone(dono.agent_id)


# ---------------------------------------------------------------------------
# emitir_deny - AC-011
# ---------------------------------------------------------------------------


class TestEmitirDeny(unittest.TestCase):
    def test_deny_tem_involucro_completo(self):
        "@spec:AC-011 a saida de deny tem o invólucro completo (estrutura, nao substring)"
        _, saida = _capturar(hookio.emitir_deny, "PreToolUse", "recurso em uso pela peer")
        obj = _unica_linha_json(saida)

        self.assertIn("hookSpecificOutput", obj)
        hso = obj["hookSpecificOutput"]
        self.assertEqual(hso.get("hookEventName"), "PreToolUse")
        self.assertEqual(hso.get("permissionDecision"), "deny")
        self.assertEqual(hso.get("permissionDecisionReason"), "recurso em uso pela peer")
        # a estrutura inteira, nao so um trecho: exatamente estas chaves.
        self.assertEqual(
            set(hso.keys()), {"hookEventName", "permissionDecision", "permissionDecisionReason"}
        )
        self.assertEqual(set(obj.keys()), {"hookSpecificOutput"})

    def test_deny_sem_involucro_seria_ignorado_por_isso_o_teste_e_estrutural(self):
        "@spec:AC-011 sem o campo permissionDecision dentro do invólucro, o harness ignoraria"
        _, saida = _capturar(hookio.emitir_deny, "PreToolUse", "x")
        obj = _unica_linha_json(saida)
        # nao basta a palavra 'deny' aparecer em algum lugar do texto: tem
        # que estar exatamente no campo que o harness le.
        self.assertEqual(obj["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_deny_em_session_end_degrada_para_silencio(self):
        "SessionEnd rejeita o invólucro na validacao -> emitir_deny degrada, nao arrisca formato invalido"
        _, saida = _capturar(hookio.emitir_deny, "SessionEnd", "qualquer razao")
        obj = _unica_linha_json(saida)
        self.assertNotIn("hookSpecificOutput", obj)

    def test_deny_evento_vazio_degrada_para_silencio(self):
        "evento vazio/desconhecido nunca produz um invólucro sem hookEventName valido"
        _, saida = _capturar(hookio.emitir_deny, "", "razao qualquer")
        obj = _unica_linha_json(saida)
        self.assertNotIn("hookSpecificOutput", obj)

    def test_razao_truncada_em_2000_chars(self):
        "razao maior que o teto do harness (2000 chars) sai truncada"
        _, saida = _capturar(hookio.emitir_deny, "PreToolUse", "x" * 5000)
        obj = _unica_linha_json(saida)
        self.assertLessEqual(len(obj["hookSpecificOutput"]["permissionDecisionReason"]), 2000)

    def test_razao_truncada_em_20_linhas(self):
        "razao com mais de 20 linhas sai truncada"
        razao = "\n".join(f"linha {i}" for i in range(50))
        _, saida = _capturar(hookio.emitir_deny, "PreToolUse", razao)
        obj = _unica_linha_json(saida)
        reason = obj["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertLessEqual(len(reason.splitlines()), 20)


# ---------------------------------------------------------------------------
# emitir_contexto
# ---------------------------------------------------------------------------


class TestEmitirContexto(unittest.TestCase):
    def test_contexto_em_evento_com_canal_confirmado(self):
        "additionalContext e emitido nos eventos com canal medido (PreToolUse)"
        _, saida = _capturar(hookio.emitir_contexto, "PreToolUse", "aviso qualquer")
        obj = _unica_linha_json(saida)
        self.assertEqual(obj["hookSpecificOutput"]["additionalContext"], "aviso qualquer")
        self.assertEqual(obj["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_contexto_em_post_tool_batch(self):
        "additionalContext tambem sai em PostToolBatch (ponto barato de aviso agregado)"
        _, saida = _capturar(hookio.emitir_contexto, "PostToolBatch", "aviso em lote")
        obj = _unica_linha_json(saida)
        self.assertEqual(obj["hookSpecificOutput"]["additionalContext"], "aviso em lote")

    def test_contexto_truncado_em_8000_chars(self):
        "additionalContext maior que o teto do harness (8000 chars) sai truncado"
        _, saida = _capturar(hookio.emitir_contexto, "PostToolUse", "y" * 9000)
        obj = _unica_linha_json(saida)
        self.assertLessEqual(len(obj["hookSpecificOutput"]["additionalContext"]), 8000)

    def test_contexto_truncado_em_200_linhas(self):
        "additionalContext com mais de 200 linhas sai truncado"
        texto = "\n".join(f"linha {i}" for i in range(300))
        _, saida = _capturar(hookio.emitir_contexto, "PostToolUse", texto)
        obj = _unica_linha_json(saida)
        self.assertLessEqual(len(obj["hookSpecificOutput"]["additionalContext"].splitlines()), 200)

    def test_stop_nunca_emite_contexto(self):
        "@spec:AC-013 additionalContext pedido em Stop degrada para silencio (medido: laco de 10 reentradas)"
        _, saida = _capturar(hookio.emitir_contexto, "Stop", "algum aviso")
        obj = _unica_linha_json(saida)
        self.assertNotIn("hookSpecificOutput", obj)
        self.assertNotIn("additionalContext", saida)

    def test_subagent_stop_nunca_emite_contexto(self):
        "additionalContext pedido em SubagentStop tambem degrada (mesma armadilha do Stop)"
        _, saida = _capturar(hookio.emitir_contexto, "SubagentStop", "algum aviso")
        obj = _unica_linha_json(saida)
        self.assertNotIn("hookSpecificOutput", obj)

    def test_session_end_nunca_emite_contexto(self):
        "additionalContext pedido em SessionEnd degrada (sem canal e sem invólucro)"
        _, saida = _capturar(hookio.emitir_contexto, "SessionEnd", "algum aviso")
        obj = _unica_linha_json(saida)
        self.assertNotIn("hookSpecificOutput", obj)

    def test_file_changed_nunca_emite_contexto(self):
        "FileChanged nao tem canal de volta ao modelo (medido) -> degrada para silencio"
        _, saida = _capturar(hookio.emitir_contexto, "FileChanged", "algum aviso")
        obj = _unica_linha_json(saida)
        self.assertNotIn("hookSpecificOutput", obj)

    def test_subagent_start_nunca_emite_contexto(self):
        (
            "achado auditoria hookio #2: SubagentStart so aparece na secao 5 "
            "de medicao-hooks.md (design prospectivo), nunca na tabela medida "
            "da secao 3 -- ate ser medido de verdade (grep de transcript), "
            "este modulo trata como sem canal confirmado e degrada p/ silencio"
        )
        _, saida = _capturar(hookio.emitir_contexto, "SubagentStart", "mapa de peers")
        obj = _unica_linha_json(saida)
        self.assertNotIn("hookSpecificOutput", obj)


# ---------------------------------------------------------------------------
# emitir_silencio
# ---------------------------------------------------------------------------


class TestEmitirSilencio(unittest.TestCase):
    def test_silencio_e_uma_linha_de_json_valido_sem_involucro(self):
        "emitir_silencio imprime uma linha de JSON valido, sem hookSpecificOutput"
        _, saida = _capturar(hookio.emitir_silencio)
        obj = _unica_linha_json(saida)
        self.assertNotIn("hookSpecificOutput", obj)


# ---------------------------------------------------------------------------
# executar - AC-013, AC-014, exceções, payload malformado
# ---------------------------------------------------------------------------


class TestExecutar(unittest.TestCase):
    def test_stop_nao_contem_additionalcontext_nem_hookspecificoutput(self):
        "@spec:AC-013 a saida para Stop nao contem additionalContext nem hookSpecificOutput"
        payload = {"hook_event_name": "Stop", "stop_hook_active": False}

        def decisor(_payload):
            # decisor mal-comportado pedindo contexto num Stop - tem que ser ignorado.
            return Decision(verdict="warn", reason="TENTATIVA DE CONTEXTO", severity="info", resource="x")

        codigo, saida = _capturar(hookio.executar, payload, decisor)
        self.assertEqual(codigo, 0)
        self.assertNotIn("hookSpecificOutput", saida)
        self.assertNotIn("additionalContext", saida)

    def test_subagent_stop_tambem_nao_emite_involucro_mesmo_com_deny_pedido(self):
        "SubagentStop nunca emite hookSpecificOutput, mesmo com decisor pedindo deny"
        payload = {"hook_event_name": "SubagentStop"}

        def decisor(_payload):
            return Decision(verdict="deny", reason="nao devia aparecer", severity="forte", resource="x")

        codigo, saida = _capturar(hookio.executar, payload, decisor)
        self.assertEqual(codigo, 0)
        self.assertNotIn("hookSpecificOutput", saida)

    def test_session_end_nao_contem_hookspecificoutput(self):
        "@spec:AC-014 a saida para SessionEnd nao contem hookSpecificOutput"
        payload = {"hook_event_name": "SessionEnd", "reason": "other"}

        def decisor(_payload):
            return Decision(verdict="deny", reason="nao devia aparecer", severity="forte", resource="x")

        codigo, saida = _capturar(hookio.executar, payload, decisor)
        self.assertEqual(codigo, 0)
        self.assertNotIn("hookSpecificOutput", saida)

    def test_pretooluse_deny_emite_involucro_completo(self):
        "PreToolUse com decisor deny emite o invólucro completo"
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash"}

        def decisor(_payload):
            return Decision(verdict="deny", reason="kill recusado", severity="forte", resource="process:x")

        _, saida = _capturar(hookio.executar, payload, decisor)
        obj = _unica_linha_json(saida)
        self.assertEqual(obj["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(obj["hookSpecificOutput"]["permissionDecisionReason"], "kill recusado")

    def test_deny_explicito_devolve_exit_diferente_de_zero(self):
        "deny efetivamente emitido devolve exit != 0; os demais casos devolvem 0"
        payload = {"hook_event_name": "PreToolUse"}

        def decisor(_payload):
            return Decision(verdict="deny", reason="kill recusado", severity="forte", resource="process:x")

        codigo, _ = _capturar(hookio.executar, payload, decisor)
        self.assertNotEqual(codigo, 0)

    def test_pretooluse_warn_emite_contexto(self):
        "PreToolUse com decisor warn emite additionalContext"
        payload = {"hook_event_name": "PreToolUse"}

        def decisor(_payload):
            return Decision(verdict="warn", reason="peer tambem esta aqui", severity="info", resource="file:x")

        codigo, saida = _capturar(hookio.executar, payload, decisor)
        self.assertEqual(codigo, 0)
        obj = _unica_linha_json(saida)
        self.assertEqual(obj["hookSpecificOutput"]["additionalContext"], "peer tambem esta aqui")

    def test_pretooluse_allow_fica_em_silencio(self):
        "PreToolUse com decisor allow nao emite invólucro"
        payload = {"hook_event_name": "PreToolUse"}

        def decisor(_payload):
            return Decision(verdict="allow", reason="ok", severity="info", resource="file:x")

        codigo, saida = _capturar(hookio.executar, payload, decisor)
        self.assertEqual(codigo, 0)
        self.assertNotIn("hookSpecificOutput", saida)

    def test_decisor_none_fica_em_silencio(self):
        "decisor que devolve None nao emite invólucro e nao levanta"
        payload = {"hook_event_name": "PreToolUse"}
        codigo, saida = _capturar(hookio.executar, payload, lambda p: None)
        self.assertEqual(codigo, 0)
        self.assertNotIn("hookSpecificOutput", saida)

    def test_excecao_do_decisor_vira_exit_0_silencioso(self):
        "excecao interna do decisor nunca quebra o turno: exit 0, sem traceback no stdout"
        payload = {"hook_event_name": "PreToolUse"}

        def decisor_quebrado(_payload):
            raise RuntimeError("bug proposital no decisor")

        codigo, saida = _capturar(hookio.executar, payload, decisor_quebrado)
        self.assertEqual(codigo, 0)
        self.assertNotIn("Traceback", saida)
        self.assertNotIn("hookSpecificOutput", saida)

    def test_payload_vazio_nao_levanta_e_sai_0(self):
        "payload vazio (dict {}) nao levanta e devolve exit 0"
        codigo, _ = _capturar(hookio.executar, {}, lambda p: None)
        self.assertEqual(codigo, 0)

    def test_payload_none_nao_levanta(self):
        "payload None (defensivo) nao levanta excecao"
        codigo, _ = _capturar(hookio.executar, None, lambda p: None)
        self.assertEqual(codigo, 0)

    def test_payload_sem_hook_event_name_nao_levanta(self):
        "payload sem hook_event_name (chave ausente) nao levanta, cai em silencio"
        codigo, saida = _capturar(hookio.executar, {"cwd": "C:\\dev"}, lambda p: None)
        self.assertEqual(codigo, 0)
        self.assertNotIn("hookSpecificOutput", saida)

    def test_erro_do_decisor_e_registrado_em_events_log(self):
        "excecao do decisor fica registrada em events.log (CCOORD_HOME), nao so engolida em silencio"
        home = tempfile.mkdtemp(prefix="ccoord_test_hookio_")
        antigo = os.environ.get("CCOORD_HOME")
        os.environ["CCOORD_HOME"] = home
        try:
            payload = {"hook_event_name": "PreToolUse", "session_id": "s1"}

            def decisor_quebrado(_payload):
                raise ValueError("falha proposital")

            _capturar(hookio.executar, payload, decisor_quebrado)

            caminho_log = os.path.join(home, "events.log")
            self.assertTrue(os.path.exists(caminho_log))
            with open(caminho_log, "r", encoding="utf-8") as fh:
                linhas = [json.loads(l) for l in fh if l.strip()]
            self.assertTrue(any(l.get("event") == "error" for l in linhas))
        finally:
            if antigo is None:
                os.environ.pop("CCOORD_HOME", None)
            else:
                os.environ["CCOORD_HOME"] = antigo

    def test_registro_de_erro_acumula_nunca_trunca(self):
        "dois erros seguidos deixam DUAS linhas: o events.log e append-only"
        # Mutante `O_APPEND` -> `O_TRUNC` em `_registrar_erro` sobrevivia a toda
        # a suite (medido em 18/09). O dano e desproporcional ao tamanho da
        # mudanca: em producao esse arquivo tem 4,48 MB e e a unica fonte de
        # medicao desta feature. Truncar apaga a evidencia sem sinal nenhum --
        # e o sintoma ("o log so tem uma linha") nao aponta para a causa.
        home = tempfile.mkdtemp(prefix="ccoord_test_hookio_append_")
        antigo = os.environ.get("CCOORD_HOME")
        os.environ["CCOORD_HOME"] = home
        try:
            def decisor_quebrado(_payload):
                raise ValueError("falha proposital")

            for i in range(2):
                _capturar(
                    hookio.executar,
                    {"hook_event_name": "PreToolUse", "session_id": f"s{i}"},
                    decisor_quebrado,
                )

            with open(os.path.join(home, "events.log"), "r", encoding="utf-8") as fh:
                erros = [
                    json.loads(l)
                    for l in fh
                    if l.strip() and json.loads(l).get("event") == "error"
                ]
            self.assertEqual(
                len(erros),
                2,
                f"esperava 2 linhas de erro acumuladas, veio {len(erros)} -- se "
                "veio 1, a escrita esta TRUNCANDO em vez de anexar",
            )
            # e as duas linhas tem de ser de sessoes DIFERENTES: se o teste
            # passasse com duas copias da mesma, ele nao provaria acumulo.
            self.assertEqual(
                sorted(l.get("session_id") for l in erros), ["s0", "s1"]
            )
        finally:
            if antigo is None:
                os.environ.pop("CCOORD_HOME", None)
            else:
                os.environ["CCOORD_HOME"] = antigo


if __name__ == "__main__":
    unittest.main()
