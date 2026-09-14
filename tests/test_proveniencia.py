"""Eixo de PROVENIENCIA: decisao minha x ordem do Vinicius (T-020).

Defeito medido no ensaio T-013 (12/09): o Vinicius mandou `taskkill /F /PID
20448` com todas as letras, o gate devolveu `deny` e a razao terminou em
*"pergunte ao Vinicius antes de matar"* -- pedir a autorizacao de quem acabou de
dar a ordem. Atrito puro, sem evitar perda nenhuma: o desfecho previsivel e ele
matar o processo por fora, onde o gate nao ve nada, e a peer perde o trabalho do
mesmo jeito e sem registro.

A correcao NAO e afrouxar o deny. E separar os dois casos:
- kill que nasce de INFERENCIA minha ("esse processo parece orfao")  -> deny;
- kill que o usuario NOMEOU no turno                                  -> warn.

O que autoriza e o alvo nomeado, nao a vontade de matar: "mata esse bug" nao
libera `process:20448`. Exige VERBO de kill + ALVO que casa com o recurso.
"""

try:
    from . import _guarda  # noqa: F401
except ImportError:
    import _guarda  # noqa: F401


import json
import os
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(RAIZ), "src")
sys.path.insert(0, SRC)

from ccoord import policy  # noqa: E402
from ccoord.classify import Resource  # noqa: E402
from ccoord.proveniencia import (  # noqa: E402
    kill_autorizado_pelo_usuario,
    ultimo_prompt_do_usuario,
)


class _DonoFalso:
    def __init__(self, session_id="peer-1", name="peer-viva"):
        self.session_id = session_id
        self.name = name
        self.pid = 999
        self.agent_id = None


class _ClaimFalso:
    def __init__(self):
        self.owner = _DonoFalso()
        self.range = None


class _SessaoFalsa:
    def __init__(self, session_id="eu", name="eu", cwd=None):
        self.session_id = session_id
        self.name = name
        self.cwd = cwd
        self.pid = 1


class TestAlvoNomeadoPeloUsuario(unittest.TestCase):
    def test_pid_literal_no_pedido_autoriza_aquele_pid(self):
        "@spec:AC-018 PID que o usuario escreveu no turno casa com o recurso do kill"
        pedido = "Rode no Bash: taskkill /F /PID 20448"
        self.assertTrue(kill_autorizado_pelo_usuario("process:20448", pedido))

    def test_nome_do_processo_no_pedido_autoriza_aquele_browser(self):
        "@spec:AC-018 nome do processo dito pelo usuario casa com o recurso"
        self.assertTrue(kill_autorizado_pelo_usuario("browser:chrome", "mata o chrome, travou"))

    def test_alvo_diferente_nao_e_autorizado(self):
        "@spec:AC-018 usuario pediu OUTRO alvo: este kill continua sendo decisao minha"
        pedido = "mata o processo 111 que travou"
        self.assertFalse(
            kill_autorizado_pelo_usuario("process:20448", pedido),
            "matar um PID porque o usuario citou outro e inferencia minha, nao ordem dele",
        )

    def test_verbo_de_kill_sem_alvo_nao_autoriza_nada(self):
        "@spec:AC-018 vontade de matar sem alvo nomeado nao libera kill (nao-vacuidade)"
        for pedido in ("mata esse bug", "kill the flaky test", "encerra o assunto"):
            with self.subTest(pedido=pedido):
                self.assertFalse(kill_autorizado_pelo_usuario("process:20448", pedido))
                self.assertFalse(kill_autorizado_pelo_usuario("browser:chrome", pedido))

    def test_alvo_sem_verbo_de_kill_nao_autoriza(self):
        "@spec:AC-018 citar o processo sem pedir kill nao autoriza kill"
        pedido = "o chrome esta consumindo 2GB, da uma olhada"
        self.assertFalse(kill_autorizado_pelo_usuario("browser:chrome", pedido))

    def test_pid_dentro_de_outro_numero_nao_casa(self):
        "@spec:AC-018 PID 448 nao pode casar por estar dentro de 20448 (fronteira de palavra)"
        self.assertFalse(kill_autorizado_pelo_usuario("process:448", "taskkill /F /PID 20448"))

    def test_prompt_vazio_ou_ausente_nunca_autoriza(self):
        "@spec:AC-018 sem prompt legivel, o kill segue sendo decisao minha (fail-closed)"
        for texto in ("", None):
            with self.subTest(texto=texto):
                self.assertFalse(kill_autorizado_pelo_usuario("process:20448", texto))


class TestLeituraDoTranscript(unittest.TestCase):
    def _transcript(self, linhas):
        fd, caminho = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for linha in linhas:
                f.write(json.dumps(linha, ensure_ascii=False) + "\n")
        self.addCleanup(os.unlink, caminho)
        return caminho

    def test_pega_a_ultima_mensagem_do_usuario(self):
        "@spec:AC-018 le a ULTIMA fala do usuario, nao a primeira nem a resposta do modelo"
        caminho = self._transcript(
            [
                {"type": "user", "message": {"role": "user", "content": "primeira coisa"}},
                {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
                {"type": "user", "message": {"role": "user", "content": "mata o chrome"}},
            ]
        )
        self.assertIn("mata o chrome", ultimo_prompt_do_usuario(caminho))
        self.assertNotIn("primeira coisa", ultimo_prompt_do_usuario(caminho))

    def test_tool_result_nao_conta_como_fala_do_usuario(self):
        "@spec:AC-018 tool_result vem com role=user no transcript e NAO e ordem do Vinicius"
        # Sem este filtro, a saida de um comando que por acaso contenha
        # "taskkill /PID 20448" autorizaria o kill -- eu autorizando a mim mesma.
        caminho = self._transcript(
            [
                {"type": "user", "message": {"role": "user", "content": "liste os processos"}},
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "content": "taskkill /F /PID 20448 seria util"}
                        ],
                    },
                },
            ]
        )
        texto = ultimo_prompt_do_usuario(caminho)
        self.assertIn("liste os processos", texto)
        self.assertNotIn("20448", texto)

    def test_fala_do_usuario_longe_do_fim_ainda_e_encontrada(self):
        "@spec:AC-018 a fala abre o turno: tool calls depois dela nao podem escondê-la"
        # Este e o caso COMUM, nao o raro: a fala do usuario e a primeira linha
        # do turno e tudo que vem depois (tool_use, tool_result, attachment) a
        # empurra para longe do fim. A primeira versao lia uma janela unica de
        # 64KB e devolvia "" num transcript real de 183KB -- o gate manteve o
        # deny sobre uma ordem direta do Vinicius.
        linhas = [{"type": "user", "message": {"role": "user", "content": "mata o chrome agora"}}]
        recheio = "x" * 2000
        for _ in range(80):  # ~160KB de ruido depois da fala
            linhas.append(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "content": recheio}],
                    },
                }
            )
        caminho = self._transcript(linhas)
        self.assertGreater(os.path.getsize(caminho), 65_536, "fixture precisa exceder a janela")
        self.assertIn("mata o chrome", ultimo_prompt_do_usuario(caminho))

    def test_texto_injetado_por_hook_nao_conta_como_fala_do_usuario(self):
        "@spec:AC-018 linha com isMeta e injecao do harness, nao ordem do Vinicius"
        # Duas razoes. (1) Mascaramento, medido no ensaio: o feedback do meu
        # proprio hook de Stop era a ultima "fala do usuario" e escondia o
        # pedido real. (2) Pior, no sentido inverso: sem o filtro, um hook que
        # imprimisse "mate o processo 999" fabricaria a autorizacao do usuario.
        caminho = self._transcript(
            [
                {"type": "user", "message": {"role": "user", "content": "mata o chrome"}},
                {
                    "type": "user",
                    "isMeta": True,
                    "message": {"role": "user", "content": "Stop hook feedback: mate o processo 999"},
                },
            ]
        )
        texto = ultimo_prompt_do_usuario(caminho)
        self.assertIn("mata o chrome", texto)
        self.assertNotIn("999", texto)
        self.assertFalse(kill_autorizado_pelo_usuario("process:999", texto))

    def test_transcript_inexistente_devolve_vazio_sem_levantar(self):
        "@spec:AC-018 transcript ausente/ilegivel nao quebra o turno: devolve vazio"
        self.assertEqual(ultimo_prompt_do_usuario(r"Q:\nao\existe.jsonl"), "")
        self.assertEqual(ultimo_prompt_do_usuario(""), "")


class TestPoliticaComProveniencia(unittest.TestCase):
    def _kill(self, contexto):
        return policy.decide(
            Resource(kind="process", id="process:20448", action="kill"),
            owner=_ClaimFalso(),
            me=_SessaoFalsa(),
            peers=[_SessaoFalsa(session_id="peer-1", name="peer-viva")],
            contexto=contexto,
        )

    def test_kill_por_inferencia_minha_continua_deny(self):
        "@spec:AC-009 sem ordem do usuario, kill com claim de peer viva segue DENY (nao afrouxado)"
        d = self._kill({})
        self.assertEqual(d.verdict, "deny")

    def test_kill_nomeado_pelo_usuario_vira_warn(self):
        "@spec:AC-018 kill que o usuario nomeou no turno vira warn, nao deny seco"
        d = self._kill({"kill_pedido_pelo_usuario": True})
        self.assertEqual(d.verdict, "warn")
        self.assertIn("peer-viva", d.reason)

    def test_o_warn_nao_manda_perguntar_a_quem_ja_pediu(self):
        "@spec:AC-018 a razao do warn nao pede autorizacao de quem deu a ordem"
        d = self._kill({"kill_pedido_pelo_usuario": True})
        self.assertNotIn("pergunte ao Vinicius", d.reason)
        self.assertIn("peer-viva", d.reason)

    def test_estado_ilegivel_vence_a_proveniencia(self):
        "@spec:AC-010 fail-closed nao e destravado por ordem do usuario"
        # Aqui nao da para saber SE existe dono: liberar seria decidir no escuro.
        d = self._kill({"kill_pedido_pelo_usuario": True, "estado_ilegivel": True})
        self.assertEqual(d.verdict, "deny")


class TestPeerNaHomeNaoBloqueiaRepo(unittest.TestCase):
    """Falso positivo achado pelo USO real (12/09), nao por teste.

    O gate recusou o meu proprio `git push` do cc-coord porque havia duas
    sessoes ociosas em `C:\\Users\\ViniciusMoraisHDT`. A condicao antiga tratava
    peer em diretorio ANCESTRAL como "no mesmo repositorio" -- e a home e
    ancestral de tudo, entao uma sessao parada ali bloqueava commit e push de
    qualquer repo da maquina. Atrito puro, exatamente o defeito que a politica
    existe para evitar.
    """

    def _commit_em(self, repo, cwd_da_peer):
        return policy.decide(
            Resource(kind="git", id="git:" + repo.lower().replace("\\", "-"), path=repo, action="commit"),
            owner=None,
            me=_SessaoFalsa(session_id="eu", cwd=repo),
            peers=[_SessaoFalsa(session_id="peer-1", name="peer-viva", cwd=cwd_da_peer)],
            contexto={},
        )

    def test_peer_na_home_nao_bloqueia_commit_em_repo_abaixo(self):
        "@spec:AC-007 sessao na HOME nao conta como sessao no repo"
        d = self._commit_em(r"C:\Users\Vinicius\dev\cc-coord", r"C:\Users\Vinicius")
        self.assertEqual(d.verdict, "allow", "sessao parada na home nao pode travar a maquina inteira")

    def test_peer_dentro_do_repo_continua_bloqueando(self):
        "@spec:AC-007 peer NO repo segue gerando deny (nao-vacuidade)"
        d = self._commit_em(r"C:\Users\Vinicius\dev\cc-coord", r"C:\Users\Vinicius\dev\cc-coord")
        self.assertEqual(d.verdict, "deny")

    def test_peer_em_subdiretorio_do_repo_tambem_bloqueia(self):
        "@spec:AC-007 peer em subpasta do repo esta no repo"
        d = self._commit_em(r"C:\Users\Vinicius\dev\cc-coord", r"C:\Users\Vinicius\dev\cc-coord\src")
        self.assertEqual(d.verdict, "deny")

    def test_repo_irmao_nao_bloqueia(self):
        "@spec:AC-007 repo vizinho nao e o mesmo repo"
        d = self._commit_em(r"C:\Users\Vinicius\dev\cc-coord", r"C:\Users\Vinicius\dev\outro")
        self.assertEqual(d.verdict, "allow")


class TestCommitSeletivoNaoEPunido(unittest.TestCase):
    """Achado do ensaio T-013 (12/09): o gate punia quem fazia certo.

    `git add <arquivo próprio> && git commit` recebia o mesmo deny de
    `git commit -am`. Quem se deu ao trabalho de ser seletivo era tratado igual
    a quem varre o WIP alheio, e o único caminho que sobrava era ignorar o gate.
    """

    def _commit(self, contexto):
        return policy.decide(
            Resource(kind="git", id="git:repo", path=r"C:\dev\repo", action="commit"),
            owner=None,
            me=_SessaoFalsa(session_id="eu", cwd=r"C:\dev\repo"),
            peers=[_SessaoFalsa(session_id="peer-1", name="peer-viva", cwd=r"C:\dev\repo")],
            contexto=contexto,
        )

    def test_commit_com_pathspec_sem_claim_alheio_vira_aviso(self):
        "@spec:AC-007 commit seletivo sem interseção com claim de peer vira warn, não deny"
        d = self._commit({"commit_seletivo_seguro": True, "pathspecs": ["notas_b.txt"]})
        self.assertEqual(d.verdict, "warn")
        self.assertIn("notas_b.txt", d.reason)

    def test_commit_sem_pathspec_continua_deny(self):
        "@spec:AC-007 commit de índice implícito com peer viva segue deny (não-vacuidade)"
        self.assertEqual(self._commit({}).verdict, "deny")

    def test_push_nunca_vira_aviso_por_pathspec(self):
        "@spec:AC-007 push com peer viva segue deny: o risco dele é levar commit alheio"
        d = policy.decide(
            Resource(kind="git", id="git:repo", path=r"C:\dev\repo", action="push"),
            owner=None,
            me=_SessaoFalsa(session_id="eu", cwd=r"C:\dev\repo"),
            peers=[_SessaoFalsa(session_id="peer-1", name="peer-viva", cwd=r"C:\dev\repo")],
            contexto={"commit_seletivo_seguro": True, "pathspecs": ["x.txt"]},
        )
        self.assertEqual(d.verdict, "deny")

    def test_razao_do_deny_nao_oferece_saida_inexecutavel(self):
        "@spec:AC-007 a razão não manda 'finalize numa branch própria' nem 'aguarde a peer liberar'"
        # Medido no ensaio: a sessão criou a branch, remediu o índice e tomou o
        # SEGUNDO deny com a mesma sugestão. Razão que prescreve o inexecutável
        # gasta turno e corrói a confiança que faz a sessão não contornar.
        razao = self._commit({}).reason.lower()
        self.assertNotIn("branch propria", razao)
        self.assertNotIn("aguarde ela liberar", razao)
        self.assertIn("vinicius", razao)
        self.assertIn("!", razao, "a razão tem de nomear o destravamento real: ele rodar por `!`")


class TestPathspecDeCommit(unittest.TestCase):
    def _alvos(self, comando):
        from ccoord.classify import pathspecs_de_commit

        return pathspecs_de_commit(comando)

    def test_pathspec_explicito_e_reconhecido(self):
        "@spec:AC-007 `git commit <caminho> -m` expõe o caminho"
        self.assertEqual(self._alvos('git commit notas_b.txt -m "trabalho da B"'), ["notas_b.txt"])
        self.assertEqual(self._alvos('git commit -m "msg" a.txt'), ["a.txt"])

    def test_commit_sem_caminho_nao_tem_pathspec(self):
        "@spec:AC-007 commit de índice implícito não tem pathspec (é o caso perigoso)"
        self.assertEqual(self._alvos('git commit -m "msg"'), [])

    def test_dash_a_anula_o_pathspec(self):
        "@spec:AC-007 `-a` varre tudo que está tracked: pathspec junto não salva"
        # Sem esta regra, `git commit -am "x" arquivo.txt` seria lido como
        # seletivo e liberado, levando o WIP da peer junto.
        for comando in ('git commit -am "x"', 'git commit -a -m "x" a.txt', 'git commit --all -m "x"'):
            with self.subTest(comando=comando):
                self.assertEqual(self._alvos(comando), [])

    def test_argumento_de_flag_nao_vira_caminho(self):
        "@spec:AC-007 o texto de -m não pode ser confundido com caminho (não-vacuidade)"
        self.assertEqual(self._alvos("git commit -m msg"), [])
        self.assertEqual(self._alvos('git -C C:/dev/repo commit -m "x" a.txt'), ["a.txt"])


class TestAvisoDeHookDesatualizado(unittest.TestCase):
    """Protecao contra o falso verde que me pegou em 12/09.

    Editei `coord_pre_bash.py` no repo, os 256 testes passaram -- eles rodam o
    arquivo do REPO -- e a producao seguiu executando a copia antiga em
    `~/.claude/hooks/`. Gate verde sobre codigo que nao esta no ar. So apareceu
    porque fui medir o comportamento real; nenhum teste pegaria.
    """

    def _monta(self, iguais: bool):
        import hashlib
        import shutil

        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, True)
        repo_hooks = os.path.join(base, "repo", "hooks")
        src = os.path.join(base, "repo", "src")
        instalados = os.path.join(base, "claude", "hooks")
        for d in (repo_hooks, src, instalados):
            os.makedirs(d)
        with open(os.path.join(repo_hooks, "coord_x.py"), "w", encoding="utf-8") as f:
            f.write("versao nova\n")
        with open(os.path.join(instalados, "coord_x.py"), "w", encoding="utf-8") as f:
            f.write("versao nova\n" if iguais else "versao ANTIGA\n")
        del hashlib
        return src, instalados

    def _rodar(self, src, instalados):
        """Executa a funcao com `__file__` apontando para a copia instalada."""
        caminho = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "hooks",
            "coord_session_start.py",
        )
        with open(caminho, encoding="utf-8") as fh:
            codigo = fh.read()
        escopo = {
            "__file__": os.path.join(instalados, "coord_session_start.py"),
            "__name__": "hook_sob_teste",
        }
        os.environ["CCOORD_SRC"] = src
        self.addCleanup(os.environ.pop, "CCOORD_SRC", None)
        exec(compile(codigo, caminho, "exec"), escopo)  # noqa: S102
        return escopo["_entrypoints_desatualizados"]()

    def test_avisa_quando_o_hook_instalado_esta_velho(self):
        "@spec:AC-012 hook instalado diferente do repo aparece no mapa do SessionStart"
        src, instalados = self._monta(iguais=False)
        self.assertEqual(self._rodar(src, instalados), ["coord_x.py"])

    def test_nao_avisa_quando_esta_sincronizado(self):
        "@spec:AC-012 sem divergencia, nenhum aviso (nao-vacuidade)"
        src, instalados = self._monta(iguais=True)
        self.assertEqual(self._rodar(src, instalados), [])

    def test_sem_ccoord_src_nao_quebra_nem_inventa_aviso(self):
        "@spec:AC-012 sem CCOORD_SRC o aviso e omitido, nunca levanta"
        _, instalados = self._monta(iguais=False)
        os.environ.pop("CCOORD_SRC", None)
        caminho = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "hooks",
            "coord_session_start.py",
        )
        escopo = {"__file__": os.path.join(instalados, "x.py"), "__name__": "hook_sob_teste"}
        with open(caminho, encoding="utf-8") as fh:
            exec(compile(fh.read(), caminho, "exec"), escopo)  # noqa: S102
        self.assertEqual(escopo["_entrypoints_desatualizados"](), [])


if __name__ == "__main__":
    unittest.main()
