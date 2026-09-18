"""Testes de ccoord.policy (unittest, stdlib - RNF-01, nada de pytest).

policy.decide() e pura: nada aqui toca disco. Owner/Claim/Session sao
construidos diretamente como dataclasses (sem passar por claims.claim() nem
sessions._read_session_file()), exatamente como a doc do modulo pede: quem
resolve I/O e o chamador (hookio.py/entrypoints), nao os testes.
"""

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py):
# sem isto, rodar este arquivo sem CCOORD_HOME grava em ~/.claude/coord real.
try:
    from . import _guarda  # noqa: F401  (import por efeito colateral)
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401



import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(RAIZ), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ccoord import policy  # noqa: E402
from ccoord.claims import Claim, Owner  # noqa: E402
from ccoord.classify import Resource  # noqa: E402
from ccoord.sessions import Session  # noqa: E402


def _owner(session_id, pid=111, proc_start="1", name=""):
    return Owner(session_id=session_id, pid=pid, proc_start=proc_start, name=name or session_id)


def _claim(session_id, path=r"C:\dev\app.js", range_=None, name="", scope="turn"):
    return Claim(
        resource=f"file:{path}",
        path=path,
        range=range_,
        owner=_owner(session_id, name=name),
        scope=scope,
        purpose="",
        acquired_at=0,
        renewed_at=0,
        ttl_s=900,
    )


def _sessao(session_id, cwd=r"C:\dev\algum-repo", name=""):
    return Session(pid=222, session_id=session_id, cwd=cwd, name=name or session_id, status="busy")


class TestPolicyKill(unittest.TestCase):
    def test_kill_recusado_com_alternativa_e_proibicao_explicita(self):
        "@spec:AC-003 matar browser de peer viva e recusado com alternativa"
        recurso = Resource(kind="browser", id="browser:chrome", action="kill")
        dono = Claim(
            resource="browser:chrome",
            path="",
            range=None,
            owner=_owner("sessao-peer", name="home-distributed-snail"),
            scope="session",
            purpose="",
            acquired_at=0,
            renewed_at=0,
            ttl_s=3600,
        )
        peers = [_sessao("sessao-peer", name="home-distributed-snail")]
        me = _sessao("sessao-eu")

        d = policy.decide(recurso, dono, me, peers)

        self.assertEqual(d.verdict, "deny")
        self.assertEqual(d.severity, "forte")
        self.assertIn("home-distributed-snail", d.reason)
        # nomeia alternativa concreta
        self.assertIn("playwright-b", d.reason)
        # proibe o kill explicitamente e nao pede autorizacao a peer
        self.assertIn("NAO se pede a uma peer", d.reason)
        self.assertLessEqual(len(d.reason), 2000)

    def test_kill_nunca_decide_por_idade_mesmo_com_dono_aparentemente_morto(self):
        "regra 2: kill nao vira allow so porque a sessao dona nao aparece nas peers vivas"
        recurso = Resource(kind="browser", id="browser:chrome", action="kill")
        dono = Claim(
            resource="browser:chrome",
            path="",
            range=None,
            owner=_owner("sessao-orfa-aparente", name="peer-orfa"),
            scope="session",
            purpose="",
            acquired_at=0,
            renewed_at=0,
            ttl_s=3600,
        )
        # peers vazio: a sessao dona NAO aparece como viva - "todos os sinais
        # apontam para orfao" - mas o kill continua negado, nunca allow por idade.
        d = policy.decide(recurso, dono, _sessao("sessao-eu"), [])
        self.assertEqual(d.verdict, "deny")
        self.assertIn("pergunte ao Vinicius", d.reason)
        self.assertIn("NUNCA decide por idade", d.reason)

    def test_kill_recurso_livre_ou_dono_sou_eu_e_liberado(self):
        "recurso sem claim, ou cujo dono sou eu, libera o kill"
        recurso = Resource(kind="process", id="process:node", action="kill")
        # sem claim nenhuma
        d1 = policy.decide(recurso, None, _sessao("sessao-eu"), [])
        self.assertEqual(d1.verdict, "allow")

        # dono sou eu
        eu = _sessao("sessao-eu")
        meu_claim = Claim(
            resource="process:node",
            path="",
            range=None,
            owner=_owner("sessao-eu"),
            scope="session",
            purpose="",
            acquired_at=0,
            renewed_at=0,
            ttl_s=3600,
        )
        d2 = policy.decide(recurso, meu_claim, eu, [eu])
        self.assertEqual(d2.verdict, "allow")


class TestPolicyArquivo(unittest.TestCase):
    def test_edit_com_dono_avisa_nao_bloqueia(self):
        "@spec:AC-005 editar arquivo com dono avisa, nao bloqueia"
        caminho = r"C:\dev\app.js"
        recurso = Resource(kind="file", id="file:app.js", path=caminho, lines=(1650, 1660), action="edit")
        dono = _claim("sessao-peer", path=caminho, range_=(1620, 1690), name="home-joyful-comet")
        peers = [_sessao("sessao-peer", cwd=r"C:\dev", name="home-joyful-comet")]

        d = policy.decide(recurso, dono, _sessao("sessao-eu"), peers)

        self.assertEqual(d.verdict, "warn")
        self.assertNotEqual(d.verdict, "deny")
        self.assertIn("home-joyful-comet", d.reason)
        self.assertIn("1620-1690", d.reason)
        self.assertIn("SendMessage", d.reason)

    def test_write_recebe_aviso_mais_forte_que_edit(self):
        "@spec:AC-017 reescrever arquivo com dono recebe aviso mais forte que Edit"
        caminho = r"C:\dev\app.js"
        dono = _claim("sessao-peer", path=caminho, range_=(1620, 1690), name="peer-x")
        peers = [_sessao("sessao-peer", cwd=r"C:\dev", name="peer-x")]
        me = _sessao("sessao-eu")

        edit = Resource(kind="file", id="file:app.js", path=caminho, lines=(1650, 1660), action="edit")
        write = Resource(kind="file", id="file:app.js", path=caminho, lines=None, action="write")

        d_edit = policy.decide(edit, dono, me, peers)
        d_write = policy.decide(write, dono, me, peers)

        self.assertEqual(d_edit.verdict, "warn")
        self.assertEqual(d_write.verdict, "warn")
        self.assertEqual(d_edit.severity, "info")
        self.assertEqual(d_write.severity, "forte")
        self.assertIn("Edit", d_write.reason)  # sugere Edit cirurgico como alternativa

    def test_faixas_disjuntas_geram_warn_curto_e_nunca_deny(self):
        "faixas disjuntas no mesmo arquivo: warn curto, nunca deny"
        caminho = r"C:\dev\app.js"
        recurso = Resource(kind="file", id="file:app.js", path=caminho, lines=(5400, 5560), action="edit")
        dono = _claim("sessao-peer", path=caminho, range_=(1620, 1690), name="peer-x")
        peers = [_sessao("sessao-peer", cwd=r"C:\dev", name="peer-x")]

        d = policy.decide(recurso, dono, _sessao("sessao-eu"), peers)

        self.assertEqual(d.verdict, "warn")
        self.assertNotEqual(d.verdict, "deny")
        self.assertEqual(d.severity, "info")

    def test_dono_morto_nao_bloqueia_edicao(self):
        "dono morto (nao aparece em peers vivas) nunca bloqueia - vira allow"
        caminho = r"C:\dev\app.js"
        recurso = Resource(kind="file", id="file:app.js", path=caminho, lines=(10, 20), action="edit")
        dono = _claim("sessao-morta", path=caminho, range_=(10, 20), name="peer-morta")

        d = policy.decide(recurso, dono, _sessao("sessao-eu"), [])  # peers vazio -> dono nao esta vivo

        self.assertEqual(d.verdict, "allow")

    def test_dono_sou_eu_nao_bloqueia(self):
        "quando o dono do claim sou eu mesmo, nunca ha aviso/bloqueio"
        caminho = r"C:\dev\app.js"
        recurso = Resource(kind="file", id="file:app.js", path=caminho, lines=(10, 20), action="edit")
        eu = _sessao("sessao-eu")
        meu_claim = _claim("sessao-eu", path=caminho, range_=(10, 20))

        d = policy.decide(recurso, meu_claim, eu, [eu])

        self.assertEqual(d.verdict, "allow")

    def test_recurso_livre_e_allow(self):
        "sem claim nenhuma o recurso e livre - allow"
        recurso = Resource(kind="file", id="file:novo.js", path=r"C:\dev\novo.js", lines=None, action="write")
        d = policy.decide(recurso, None, _sessao("sessao-eu"), [])
        self.assertEqual(d.verdict, "allow")


class TestPolicyPorta(unittest.TestCase):
    def test_porta_ocupada_sugere_porta_livre_concreta(self):
        "@spec:AC-006 porta ocupada recebe porta livre concreta"
        recurso = Resource(kind="port", id="port:8099", action="bind")
        dono = Claim(
            resource="port:8099",
            path="port:8099",
            range=None,
            owner=_owner("sessao-peer", name="peer-porta"),
            scope="session",
            purpose="",
            acquired_at=0,
            renewed_at=0,
            ttl_s=3600,
        )
        peers = [_sessao("sessao-peer", name="peer-porta")]

        d = policy.decide(recurso, dono, _sessao("sessao-eu"), peers, contexto={"porta_livre": 8123})

        self.assertEqual(d.verdict, "warn")
        self.assertIn("8123", d.reason)
        self.assertIn("peer-porta", d.reason)

    def test_porta_ocupada_sem_contexto_ainda_sugere_numero_concreto(self):
        "sem contexto explicito, cai para fallback (porta+1), ainda um numero concreto"
        recurso = Resource(kind="port", id="port:8099", action="bind")
        dono = Claim(
            resource="port:8099",
            path="port:8099",
            range=None,
            owner=_owner("sessao-peer", name="peer-porta"),
            scope="session",
            purpose="",
            acquired_at=0,
            renewed_at=0,
            ttl_s=3600,
        )
        d = policy.decide(recurso, dono, _sessao("sessao-eu"), [_sessao("sessao-peer", name="peer-porta")])
        self.assertEqual(d.verdict, "warn")
        self.assertIn("8100", d.reason)


class TestPolicyGit(unittest.TestCase):
    def test_commit_recusado_citando_commit_alheio(self):
        "@spec:AC-007 commit nao sai com trabalho alheio no meio"
        repo = r"C:\dev\app-exemplo"
        recurso = Resource(kind="git", id="git:c--dev-app-exemplo", path=repo, action="commit")
        peers = [_sessao("sessao-peer", cwd=repo, name="peer-git")]
        me = _sessao("sessao-eu", cwd=repo)

        d = policy.decide(
            recurso,
            None,
            me,
            peers,
            contexto={"commits_alheios": ["a1b2c3d peer-git: ajuste de schema"]},
        )

        self.assertEqual(d.verdict, "deny")
        self.assertEqual(d.severity, "forte")
        self.assertIn("a1b2c3d", d.reason)
        self.assertIn("peer-git", d.reason)

    def test_push_recusado_mesmo_sem_lista_de_commits(self):
        "push tambem e recusado quando ha peer viva no mesmo repo, mesmo sem detalhe de commit"
        repo = r"C:\dev\app-exemplo"
        recurso = Resource(kind="git", id="git:c--dev-app-exemplo", path=repo, action="push")
        peers = [_sessao("sessao-peer", cwd=repo, name="peer-git")]

        d = policy.decide(recurso, None, _sessao("sessao-eu", cwd=repo), peers)

        self.assertEqual(d.verdict, "deny")

    def test_commit_sem_peer_no_repo_e_liberado(self):
        "sem peer viva no mesmo repo, commit segue liberado"
        repo = r"C:\dev\app-exemplo"
        recurso = Resource(kind="git", id="git:c--dev-app-exemplo", path=repo, action="commit")
        peers = [_sessao("sessao-peer", cwd=r"C:\dev\outro-repo", name="peer-git")]

        d = policy.decide(recurso, None, _sessao("sessao-eu", cwd=repo), peers)

        self.assertEqual(d.verdict, "allow")


class TestPolicyMigracao(unittest.TestCase):
    def test_migracao_com_peer_viva_gera_warn_forte(self):
        "migracao de schema com peer viva no repo: warn forte, adiar"
        recurso = Resource(kind="db", id="db:app-exemplo:migrations", action="migrate")
        peers = [_sessao("sessao-peer", cwd=r"C:\dev\app-exemplo", name="peer-db")]
        me = _sessao("sessao-eu", cwd=r"C:\dev\app-exemplo")

        d = policy.decide(recurso, None, me, peers)

        self.assertEqual(d.verdict, "warn")
        self.assertEqual(d.severity, "forte")
        self.assertIn("peer-db", d.reason)


class TestPolicyEstadoIlegivel(unittest.TestCase):
    def test_estado_ilegivel_libera_edit_mas_bloqueia_kill(self):
        "@spec:AC-010 estado corrompido nao trava edicao, mas trava o kill"
        edit = Resource(kind="file", id="file:app.js", path=r"C:\dev\app.js", lines=(1, 2), action="edit")
        kill = Resource(kind="browser", id="browser:chrome", action="kill")

        d_edit = policy.decide(edit, None, _sessao("sessao-eu"), [], contexto={"estado_ilegivel": True})
        d_kill = policy.decide(kill, None, _sessao("sessao-eu"), [], contexto={"estado_ilegivel": True})

        self.assertEqual(d_edit.verdict, "allow")
        self.assertEqual(d_kill.verdict, "deny")
        self.assertEqual(d_kill.severity, "forte")


class TestPolicyCamposDeSessaoMalformados(unittest.TestCase):
    """Achado [ALTA] grupo policy: registro de sessao de peer (~/.claude/sessions/
    <pid>.json) com campo de tipo errado (nao veio de `Session(...)` construida
    a mao, mas de JSON no disco fora do controle deste modulo) nao pode
    derrubar `decide()` com excecao nao tratada. Antes da correcao, `cwd`
    int/lista/dict/bool fazia `_norm()` chamar `.strip()` num tipo sem esse
    metodo (AttributeError), e `name` int/lista fazia `", ".join(sorted(...))`
    levantar TypeError -- em ambos os casos a excecao subia ate `decide()` e
    seria engolida pelo catch-all de hookio.executar, resultando em `git
    commit`/`push` saindo ALLOW em silencio mesmo com peer viva no mesmo
    repo (o deny irreversivel que a regra 1 do modulo exige). Pior: como o
    loop de peers e' unico por chamada, um so registro malformado abortava a
    checagem para TODAS as peers, inclusive as com dado valido.
    """

    def test_peer_isolada_com_cwd_tipo_errado_nao_lanca_excecao(self):
        "cwd int/lista/dict/bool isolado: decide() nunca lanca, e cai em allow (sem info suficiente p/ provar mesmo repo)"
        repo = r"C:\dev\app-exemplo"
        recurso = Resource(kind="git", id="git:c--dev-app-exemplo", path=repo, action="commit")
        me = _sessao("sessao-eu", cwd=repo)

        for cwd_malformado in (12345, [repo], {"x": 1}, True, 3.14):
            with self.subTest(cwd=cwd_malformado):
                peer = Session(pid=222, session_id="sessao-peer", cwd=cwd_malformado, name="peer-git", status="busy")
                d = policy.decide(recurso, None, me, [peer])  # nao pode lancar
                self.assertEqual(d.verdict, "allow")

    def test_peer_malformada_nao_derruba_deny_de_peer_valida_no_mesmo_repo(self):
        "uma peer com cwd de tipo errado na lista nao pode fazer a checagem abortar para as demais peers (validas)"
        repo = r"C:\dev\app-exemplo"
        recurso = Resource(kind="git", id="git:c--dev-app-exemplo", path=repo, action="commit")
        me = _sessao("sessao-eu", cwd=repo)
        peer_valida = _sessao("sessao-peer-valida", cwd=repo, name="peer-valida")
        peer_malformada = Session(pid=225, session_id="sessao-peer-malformada", cwd=999, name="peer-ruim", status="busy")

        d = policy.decide(recurso, None, me, [peer_valida, peer_malformada])

        self.assertEqual(d.verdict, "deny")
        self.assertIn("peer-valida", d.reason)

    def test_peer_com_name_tipo_errado_nao_lanca_e_preserva_deny(self):
        "name int/lista (em vez de str) nao pode quebrar o join/sorted dos nomes nem derrubar o deny"
        repo = r"C:\dev\app-exemplo"
        recurso = Resource(kind="git", id="git:c--dev-app-exemplo", path=repo, action="commit")
        me = _sessao("sessao-eu", cwd=repo)

        for name_malformado in (12345, [1, 2]):
            with self.subTest(name=name_malformado):
                peer = Session(pid=226, session_id="sessao-peer-nome-ruim", cwd=repo, name=name_malformado, status="busy")
                d = policy.decide(recurso, None, me, [peer])  # nao pode lancar
                self.assertEqual(d.verdict, "deny")

    def test_cwd_tipo_errado_tambem_nao_quebra_verificacao_de_migracao(self):
        "_reponame_do_cwd reusa _norm() -- o mesmo defeito de cwd tambem alcancava o ramo de migracao"
        recurso = Resource(kind="db", id="db:app-exemplo:migrations", action="migrate")
        me = _sessao("sessao-eu", cwd=r"C:\dev\app-exemplo")
        peer_malformada = Session(pid=227, session_id="sessao-peer-mig", cwd=[1, 2, 3], name="peer-mig", status="busy")

        d = policy.decide(recurso, None, me, [peer_malformada])  # nao pode lancar

        self.assertEqual(d.verdict, "allow")


class TestPolicyRazaoTruncada(unittest.TestCase):
    def test_razao_nunca_estoura_o_teto_do_harness(self):
        "razao sempre <= 2000 chars / 20 linhas, mesmo com contexto gigante"
        repo = r"C:\dev\app-exemplo"
        recurso = Resource(kind="git", id="git:c--dev-app-exemplo", path=repo, action="commit")
        peers = [_sessao("sessao-peer", cwd=repo, name="peer-git")]
        commits_gigantes = [f"commit-{i} " + ("x" * 200) for i in range(50)]

        d = policy.decide(
            recurso, None, _sessao("sessao-eu", cwd=repo), peers, contexto={"commits_alheios": commits_gigantes}
        )

        self.assertLessEqual(len(d.reason), 2000)
        self.assertLessEqual(len(d.reason.splitlines()), 20)


if __name__ == "__main__":
    unittest.main()
