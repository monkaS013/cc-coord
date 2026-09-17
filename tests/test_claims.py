"""Testes de ccoord.claims (unittest, stdlib - RNF-01, nada de pytest)."""

# Isolamento do estado ANTES de qualquer import de ccoord (ver tests/_guarda.py):
# sem isto, rodar este arquivo sem CCOORD_HOME grava em ~/.claude/coord real.
try:
    from . import _guarda  # noqa: F401  (import por efeito colateral)
except ImportError:  # carregado solto (unittest discover -s tests, sem -t)
    import _guarda  # noqa: F401



import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(RAIZ), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ccoord import claims  # noqa: E402


def _owner(session_id, pid=None, proc_start="1", name="", agent_id=None):
    return claims.Owner(
        session_id=session_id,
        pid=pid if pid is not None else os.getpid(),
        proc_start=proc_start,
        name=name or session_id,
        agent_id=agent_id,
    )


def _ler_eventos(home):
    caminho = os.path.join(home, "events.log")
    if not os.path.exists(caminho):
        return []
    linhas = []
    with open(caminho, "r", encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if linha:
                linhas.append(json.loads(linha))
    return linhas


def _disputa_worker(home, resource, indice, fila, barreira):
    """Roda em processo separado: aguarda a barreira e disputa o mesmo recurso.

    Definido no nivel do modulo (nao dentro da classe de teste) para ser
    picklable pelo multiprocessing no Windows (spawn).
    """
    os.environ["CCOORD_HOME"] = home
    sys.path.insert(0, SRC)
    from ccoord import claims as _claims  # import local: reforca isolamento no processo filho

    dono = _claims.Owner(
        session_id=f"sessao-{indice}",
        pid=20000 + indice,
        proc_start="999",
        name=f"peer-{indice}",
    )
    barreira.wait()  # sincroniza os dois processos para disputar de verdade
    resultado = _claims.claim(
        resource,
        dono,
        900,
        {"path": r"C:\dev\disputa.txt"},
        esta_vivo=lambda o: True,  # ninguem aqui esta morto - so testa a corrida
    )
    fila.put((indice, resultado.ok, resultado.claim.owner.session_id if resultado.claim else None))


class TestClaims(unittest.TestCase):
    def setUp(self):
        self._home_antigo = os.environ.get("CCOORD_HOME")
        self._tmp = tempfile.mkdtemp(prefix="ccoord_test_")
        os.environ["CCOORD_HOME"] = self._tmp

    def tearDown(self):
        if self._home_antigo is None:
            os.environ.pop("CCOORD_HOME", None)
        else:
            os.environ["CCOORD_HOME"] = self._home_antigo
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # AC-001: corrida REAL entre dois processos
    # ------------------------------------------------------------------

    def test_corrida_real_dois_processos(self):
        "@spec:AC-001 duas sessoes nao tomam o mesmo recurso"
        resource = "file:disputa.txt"
        ctx = multiprocessing.get_context("spawn")
        barreira = ctx.Barrier(2)
        fila = ctx.Queue()

        p1 = ctx.Process(target=_disputa_worker, args=(self._tmp, resource, 1, fila, barreira))
        p2 = ctx.Process(target=_disputa_worker, args=(self._tmp, resource, 2, fila, barreira))
        p1.start()
        p2.start()
        p1.join(timeout=30)
        p2.join(timeout=30)

        self.assertFalse(p1.is_alive(), "processo 1 nao terminou a tempo")
        self.assertFalse(p2.is_alive(), "processo 2 nao terminou a tempo")

        resultados = [fila.get(timeout=5), fila.get(timeout=5)]
        oks = [r for r in resultados if r[1] is True]
        negados = [r for r in resultados if r[1] is False]

        self.assertEqual(len(oks), 1, f"exatamente um deveria adquirir; obtive {resultados}")
        self.assertEqual(len(negados), 1, f"exatamente um deveria ser recusado; obtive {resultados}")

        vencedor_sessao = oks[0][0]
        # o processo recusado deve apontar para o dono CORRETO (o vencedor real)
        self.assertEqual(negados[0][2], f"sessao-{vencedor_sessao}")

        # e o registro em disco tem que concordar com quem venceu
        dono_final = claims.owner_of(resource, esta_vivo=lambda o: True)
        self.assertIsNotNone(dono_final)
        self.assertEqual(dono_final.owner.session_id, f"sessao-{vencedor_sessao}")

    # ------------------------------------------------------------------
    # AC-002: dono morto e substituido, roubo vai para events.log
    # ------------------------------------------------------------------

    def test_dono_morto_e_substituido_e_logado(self):
        "@spec:AC-002 claim de dono morto e substituida e o roubo aparece em events.log"
        resource = "file:orfao.txt"
        dono_morto = _owner("sessao-morta", pid=999999)
        dono_novo = _owner("sessao-nova", pid=os.getpid())

        r1 = claims.claim(
            resource, dono_morto, 900, {"path": r"C:\dev\orfao.txt"}, esta_vivo=lambda o: True
        )
        self.assertTrue(r1.ok)

        def morto_se_for_a_sessao_morta(o):
            return o.session_id != "sessao-morta"

        r2 = claims.claim(
            resource,
            dono_novo,
            900,
            {"path": r"C:\dev\orfao.txt"},
            esta_vivo=morto_se_for_a_sessao_morta,
        )
        self.assertTrue(r2.ok, "deveria roubar o claim do dono morto")
        self.assertEqual(r2.claim.owner.session_id, "sessao-nova")

        dono_atual = claims.owner_of(resource, esta_vivo=morto_se_for_a_sessao_morta)
        self.assertEqual(dono_atual.owner.session_id, "sessao-nova")

        eventos = _ler_eventos(self._tmp)
        roubos = [e for e in eventos if e["event"] == "steal_stale"]
        self.assertEqual(len(roubos), 1)
        self.assertEqual(roubos[0]["previous_owner"]["session_id"], "sessao-morta")
        self.assertEqual(roubos[0]["new_owner"]["session_id"], "sessao-nova")

    def test_dono_vivo_nao_e_substituido(self):
        "dono vivo recusa a disputa e devolve o dono correto (nao rouba quem esta vivo)"
        resource = "file:vivo.txt"
        dono_a = _owner("sessao-a", pid=os.getpid())
        dono_b = _owner("sessao-b", pid=os.getpid() + 1)

        r1 = claims.claim(resource, dono_a, 900, {"path": r"C:\dev\vivo.txt"}, esta_vivo=lambda o: True)
        self.assertTrue(r1.ok)

        r2 = claims.claim(resource, dono_b, 900, {"path": r"C:\dev\vivo.txt"}, esta_vivo=lambda o: True)
        self.assertFalse(r2.ok)
        self.assertEqual(r2.claim.owner.session_id, "sessao-a")

    def test_sweep_remove_dono_morto_e_loga(self):
        "sweep() remove claim de dono morto/expirado e loga steal_stale"
        resource = "file:para_varrer.txt"
        dono = _owner("sessao-varrida", pid=os.getpid())
        r1 = claims.claim(resource, dono, 900, {"path": r"C:\dev\para_varrer.txt"})
        self.assertTrue(r1.ok)

        removidos = claims.sweep(esta_vivo=lambda o: False)  # todo mundo "morto" para este teste
        self.assertEqual(removidos, 1)
        self.assertIsNone(claims.owner_of(resource, esta_vivo=lambda o: False))

        eventos = _ler_eventos(self._tmp)
        roubos = [e for e in eventos if e["event"] == "steal_stale"]
        self.assertEqual(len(roubos), 1)
        self.assertEqual(roubos[0]["previous_owner"]["session_id"], "sessao-varrida")
        self.assertIsNone(roubos[0]["new_owner"])

    # ------------------------------------------------------------------
    # AC-008: release(scope="session") nao deixa claim nenhuma da sessao
    # ------------------------------------------------------------------

    def test_release_session_remove_todas_as_claims_da_sessao(self):
        "@spec:AC-008 release(scope=session) nao deixa nenhuma claim daquela sessao"
        dono = _owner("sessao-encerrando", pid=os.getpid())
        outra_sessao = _owner("sessao-continua", pid=os.getpid() + 1)

        claims.claim(
            "file:a.txt", dono, 900, {"path": r"C:\dev\a.txt", "scope": "turn"}, esta_vivo=lambda o: True
        )
        claims.claim(
            "file:b.txt", dono, 3600, {"path": r"C:\dev\b.txt", "scope": "session"}, esta_vivo=lambda o: True
        )
        claims.claim(
            "file:c.txt",
            outra_sessao,
            900,
            {"path": r"C:\dev\c.txt", "scope": "turn"},
            esta_vivo=lambda o: True,
        )

        removidos = claims.release(dono, scope="session")
        self.assertEqual(removidos, 2)

        self.assertIsNone(claims.owner_of("file:a.txt", esta_vivo=lambda o: True))
        self.assertIsNone(claims.owner_of("file:b.txt", esta_vivo=lambda o: True))
        # a outra sessao nao foi tocada
        restante = claims.owner_of("file:c.txt", esta_vivo=lambda o: True)
        self.assertIsNotNone(restante)
        self.assertEqual(restante.owner.session_id, "sessao-continua")

    def test_release_turn_so_libera_escopo_turn(self):
        "release(scope=turn) preserva leases de recurso (scope=session) do mesmo dono"
        dono = _owner("sessao-x", pid=os.getpid())
        claims.claim(
            "file:turno.txt", dono, 900, {"path": r"C:\dev\turno.txt", "scope": "turn"}, esta_vivo=lambda o: True
        )
        claims.claim(
            "resource:lease.txt",
            dono,
            3600,
            {"path": r"C:\dev\lease.txt", "scope": "session"},
            esta_vivo=lambda o: True,
        )

        removidos = claims.release(dono, scope="turn")
        self.assertEqual(removidos, 1)
        self.assertIsNone(claims.owner_of("file:turno.txt", esta_vivo=lambda o: True))
        self.assertIsNotNone(claims.owner_of("resource:lease.txt", esta_vivo=lambda o: True))

    def test_events_log_e_append_nunca_trunca(self):
        "o events.log ACUMULA: trocar O_APPEND por O_TRUNC apagaria o historico inteiro"
        # A 3a auditoria (17/09) mediu que o mutante `O_APPEND`->`O_TRUNC`
        # sobrevive a suite INTEIRA. O dano nao e hipotetico: em producao esse
        # arquivo tem 4,48 MB e e a unica fonte de medicao desta feature --
        # todas as decisoes de desenho de 17/09 sairam dele. Um truncamento
        # apaga a evidencia e nao deixa sinal nenhum.
        dono = _owner("sessao-log", pid=os.getpid())
        for i in range(3):
            claims.claim(
                f"file:acum{i}.txt", dono, 900,
                {"path": rf"C:\dev\acum{i}.txt", "scope": "turn"},
                esta_vivo=lambda o: True,
            )

        eventos = _ler_eventos(self._tmp)
        adquiridos = [e for e in eventos if e.get("event") == "acquire"]
        self.assertGreaterEqual(
            len(adquiridos),
            3,
            "o events.log nao acumulou os 3 eventos -- se so o ultimo sobrou, "
            "a escrita esta truncando em vez de anexar",
        )

    def test_release_turn_do_main_ALCANCA_claim_de_subagente_por_padrao(self):
        "o default agente_exato=False preserva o comportamento do Stop: o main libera o do subagente"
        # Achado ALTA-3 da auditoria (17/09): flipar o default de `False` para
        # `True` sobrevivia a 113 testes. Nada provava que o `Stop` e o
        # `SessionEnd` continuavam alcancando claims de turno de subagente --
        # e sem essa prova, alguem que passasse `agente_exato=True` no
        # `coord_stop.py` "por simetria" faria os claims de subagente vazarem
        # ate o TTL de 900 s, ressuscitando o falso-"colide" que a T-032
        # existe para matar, com a suite inteira verde.
        main = _owner("sessao-y", pid=os.getpid())
        sub = _owner("sessao-y", pid=os.getpid(), agent_id="agente-7")
        claims.claim(
            "file:do-main.txt", main, 900, {"path": r"C:\dev\m.txt", "scope": "turn"},
            esta_vivo=lambda o: True,
        )
        claims.claim(
            "file:do-sub.txt", sub, 900, {"path": r"C:\dev\s.txt", "scope": "turn"},
            esta_vivo=lambda o: True,
        )

        removidos = claims.release(main, scope="turn")

        self.assertEqual(removidos, 2, "o release do Stop tem de levar os DOIS")
        self.assertIsNone(claims.owner_of("file:do-main.txt", esta_vivo=lambda o: True))
        self.assertIsNone(
            claims.owner_of("file:do-sub.txt", esta_vivo=lambda o: True),
            "claim de subagente sobreviveu ao release do main -- no Stop isso e "
            "vazamento: o turno acabou para todos",
        )

    def test_release_turn_com_agente_exato_POUPA_o_subagente(self):
        "agente_exato=True (inicio de turno) nao toca no claim de subagente"
        # O outro lado do par. Sem os dois, um unico teste nao distingue
        # "funciona" de "o parametro nao faz nada".
        main = _owner("sessao-z", pid=os.getpid())
        sub = _owner("sessao-z", pid=os.getpid(), agent_id="agente-9")
        claims.claim(
            "file:m2.txt", main, 900, {"path": r"C:\dev\m2.txt", "scope": "turn"},
            esta_vivo=lambda o: True,
        )
        claims.claim(
            "file:s2.txt", sub, 900, {"path": r"C:\dev\s2.txt", "scope": "turn"},
            esta_vivo=lambda o: True,
        )

        removidos = claims.release(main, scope="turn", agente_exato=True)

        self.assertEqual(removidos, 1, "levou mais que o claim do main")
        self.assertIsNone(claims.owner_of("file:m2.txt", esta_vivo=lambda o: True))
        self.assertIsNotNone(
            claims.owner_of("file:s2.txt", esta_vivo=lambda o: True),
            "apagou o claim do subagente: se ele for de background e ainda "
            "estiver escrevendo, a peer que editar ali ouve 'sem sobreposicao'",
        )

    def test_release_all_ignora_agent_id(self):
        "release(scope=all) varre a sessao inteira, mesmo entre agentes diferentes"
        dono_agente_1 = _owner("sessao-multi", pid=os.getpid(), agent_id="agente-1")
        dono_agente_2 = _owner("sessao-multi", pid=os.getpid(), agent_id="agente-2")

        claims.claim(
            "file:d1.txt", dono_agente_1, 900, {"path": r"C:\dev\d1.txt"}, esta_vivo=lambda o: True
        )
        claims.claim(
            "file:d2.txt", dono_agente_2, 900, {"path": r"C:\dev\d2.txt"}, esta_vivo=lambda o: True
        )

        removidos = claims.release(dono_agente_1, scope="all")
        self.assertEqual(removidos, 2)

    # ------------------------------------------------------------------
    # overlapping(): unidade de coordenacao = (arquivo, faixa de linha)
    # ------------------------------------------------------------------

    def test_overlapping_faixas_disjuntas_nao_colidem(self):
        "faixas disjuntas no mesmo arquivo nao colidem"
        caminho = r"C:\dev\app.js"
        dono_a = _owner("sessao-a", pid=os.getpid())
        dono_b = _owner("sessao-b", pid=os.getpid() + 1)

        claims.claim(
            "file:app.js:1620-1690", dono_a, 900, {"path": caminho, "range": (1620, 1690)}
        )
        claims.claim(
            "file:app.js:5400-5560", dono_b, 900, {"path": caminho, "range": (5400, 5560)}
        )

        achados_a = claims.overlapping(caminho, (1620, 1690))
        self.assertEqual([c.owner.session_id for c in achados_a], ["sessao-a"])

        achados_b = claims.overlapping(caminho, (5400, 5560))
        self.assertEqual([c.owner.session_id for c in achados_b], ["sessao-b"])

        # faixa fora das duas: nenhuma colisao
        self.assertEqual(claims.overlapping(caminho, (2000, 2100)), [])

    def test_overlapping_faixas_sobrepostas_colidem(self):
        "faixas sobrepostas no mesmo arquivo colidem"
        caminho = r"C:\dev\app.js"
        dono = _owner("sessao-a", pid=os.getpid())
        claims.claim("file:app.js:100-200", dono, 900, {"path": caminho, "range": (100, 200)})

        # sobreposicao parcial nas duas pontas
        self.assertEqual(len(claims.overlapping(caminho, (150, 250))), 1)
        self.assertEqual(len(claims.overlapping(caminho, (50, 150))), 1)
        # contida
        self.assertEqual(len(claims.overlapping(caminho, (120, 180))), 1)
        # so toca na borda (inclusive) ainda conta como colisao
        self.assertEqual(len(claims.overlapping(caminho, (200, 300))), 1)

    def test_overlapping_range_none_colide_com_tudo(self):
        "range=null (arquivo inteiro) colide com qualquer faixa, nos dois sentidos"
        caminho = r"C:\dev\app.js"
        dono = _owner("sessao-a", pid=os.getpid())

        claims.claim("file:app.js", dono, 900, {"path": caminho, "range": None})
        self.assertEqual(len(claims.overlapping(caminho, (1, 2))), 1)
        self.assertEqual(len(claims.overlapping(caminho, None)), 1)

        # e o inverso: um claim de faixa colide com uma consulta de arquivo inteiro
        dono2 = _owner("sessao-b", pid=os.getpid() + 1)
        claims.claim(
            "file:outro.js:10-20", dono2, 900, {"path": r"C:\dev\outro.js", "range": (10, 20)}
        )
        self.assertEqual(len(claims.overlapping(r"C:\dev\outro.js", None)), 1)

    def test_overlapping_ignora_caminho_diferente(self):
        "claims de outro arquivo nunca entram na resposta"
        dono = _owner("sessao-a", pid=os.getpid())
        claims.claim("file:x.js", dono, 900, {"path": r"C:\dev\x.js", "range": None})
        self.assertEqual(claims.overlapping(r"C:\dev\y.js", None), [])

    # ------------------------------------------------------------------
    # Leitura defensiva e nao-escrita fora da raiz
    # ------------------------------------------------------------------

    def test_arquivo_corrompido_nao_derruba_owner_of(self):
        "arquivo de claim corrompido e ignorado, nao propaga excecao"
        claims_dir = os.path.join(self._tmp, "claims")
        os.makedirs(claims_dir, exist_ok=True)
        ruim = os.path.join(claims_dir, "_corrompido.json")
        with open(ruim, "w", encoding="utf-8") as fh:
            fh.write("{nao e json valido")

        # nao deve levantar excecao
        self.assertEqual(claims.overlapping(r"C:\dev\qualquer.txt", None), [])

    def test_todas_as_escritas_ficam_dentro_de_ccoord_home(self):
        "nenhuma escrita (os.open/os.makedirs/os.remove) acontece fora da raiz CCOORD_HOME"
        # Achado 2 (auditoria 11/09): a versao antiga deste teste so andava
        # com `os.walk(self._tmp)` -- por construcao, um walk NUNCA encontra
        # nada fora da propria raiz que esta varrendo, entao a asserção era
        # verdadeira mesmo que a escrita real saisse 100% fora de
        # CCOORD_HOME (o walk simplesmente achava zero arquivos e o laco
        # nunca rodava). Prova: com `_home()` mutado para devolver um
        # diretorio fora daqui, o teste antigo continuava verde.
        #
        # Este teste intercepta as PRIMITIVAS de escrita de verdade
        # (os.open/os.makedirs/os.remove) e verifica que TODO caminho
        # passado a elas fica dentro de CCOORD_HOME -- pega o vazamento
        # onde quer que ele va parar, em vez de so revistar um lugar que ja
        # presume a resposta.
        caminhos_tocados = []
        os_open_original = os.open
        os_makedirs_original = os.makedirs
        os_remove_original = os.remove

        def open_espiao(caminho, *a, **kw):
            caminhos_tocados.append(caminho)
            return os_open_original(caminho, *a, **kw)

        def makedirs_espiao(caminho, *a, **kw):
            caminhos_tocados.append(caminho)
            return os_makedirs_original(caminho, *a, **kw)

        def remove_espiao(caminho, *a, **kw):
            caminhos_tocados.append(caminho)
            return os_remove_original(caminho, *a, **kw)

        # patch no MODULO os (claims.os e o mesmo objeto que o `os` daqui -
        # e o mesmo modulo em sys.modules), restaurado no finally.
        claims.os.open = open_espiao
        claims.os.makedirs = makedirs_espiao
        claims.os.remove = remove_espiao
        try:
            dono = _owner("sessao-raiz", pid=os.getpid())
            claims.claim("file:raiz.txt", dono, 900, {"path": r"C:\dev\raiz.txt"})
            claims.claim("file:raiz.txt", dono, 900, {"path": r"C:\dev\raiz.txt"})  # renova
            claims.release(dono, scope="all")
            claims.claim("file:raiz2.txt", dono, -1, {"path": r"C:\dev\raiz2.txt"})
            claims.sweep(esta_vivo=lambda o: False)  # forca uma remocao por sweep tambem
        finally:
            claims.os.open = os_open_original
            claims.os.makedirs = os_makedirs_original
            claims.os.remove = os_remove_original

        self.assertTrue(caminhos_tocados, "nenhuma escrita foi observada - o teste nao exercitou nada")
        raiz_abs = os.path.abspath(self._tmp)
        for caminho in caminhos_tocados:
            self.assertEqual(
                os.path.commonpath([os.path.abspath(caminho), raiz_abs]),
                raiz_abs,
                f"escrita fora de CCOORD_HOME detectada: {caminho!r}",
            )

    def test_esta_vivo_padrao_reconhece_processo_atual_e_pid_inexistente(self):
        "esta_vivo_padrao usa PID real: processo atual vivo, PID absurdo morto"
        # sem proc_start para comparar, o default e fail-safe: PID existe -> assume vivo
        eu_sem_proc_start = claims.Owner(session_id="eu", pid=os.getpid(), proc_start="")
        self.assertTrue(claims.esta_vivo_padrao(eu_sem_proc_start))

        # PID que quase certamente nao existe -> morto
        fantasma = claims.Owner(session_id="fantasma", pid=999999, proc_start="0")
        self.assertFalse(claims.esta_vivo_padrao(fantasma))

    # ------------------------------------------------------------------
    # Achado 1 (auditoria 11/09): _slug() colidia espaco com '_' literal
    # ------------------------------------------------------------------

    def test_slug_nao_colide_espaco_com_underscore_literal(self):
        "_slug() nao pode mapear duas resources diferentes pro mesmo slug"
        # 'Oscar Alho' (pasta real do vault, com espaco) x 'Oscar_Alho' (com
        # underscore) e o par concreto do achado: a versao antiga de _slug
        # substituia QUALQUER char fora de alnum/-._ por '_' -- mas '_' e um
        # char PERMITIDO que passa cru, entao espaco e underscore literal na
        # mesma posicao produziam o MESMO slug.
        self.assertNotEqual(claims._slug("Oscar Alho"), claims._slug("Oscar_Alho"))
        # generalizando: nenhum char literal isolado pode colidir com o
        # escape de outro char nessa posicao.
        self.assertNotEqual(claims._slug("a_b"), claims._slug("a b"))
        self.assertNotEqual(claims._slug("a.b"), claims._slug("a_b"))

    def test_claim_de_dois_arquivos_diferentes_com_espaco_e_underscore_nao_colide(self):
        "@finding-1 dois ARQUIVOS diferentes (espaco vs underscore) tem que conseguir claims independentes"
        dono_a = _owner("sessao-a", pid=os.getpid())
        dono_b = _owner("sessao-b", pid=os.getpid() + 1)

        r1 = claims.claim(
            r"file:C:\Oscar Alho\arquivo.md",
            dono_a,
            900,
            {"path": r"C:\Oscar Alho\arquivo.md"},
            esta_vivo=lambda o: True,
        )
        self.assertTrue(r1.ok)

        # arquivo DIFERENTE (pasta com underscore em vez de espaco) - tem que
        # conseguir seu proprio claim, nao pode ser recusado por causa de um
        # claim que pertence a um arquivo diferente.
        r2 = claims.claim(
            r"file:C:\Oscar_Alho\arquivo.md",
            dono_b,
            900,
            {"path": r"C:\Oscar_Alho\arquivo.md"},
            esta_vivo=lambda o: True,
        )
        self.assertTrue(r2.ok, "recurso de arquivo DIFERENTE nao pode ser negado por colisao de slug")

        dono_do_espaco = claims.owner_of(r"file:C:\Oscar Alho\arquivo.md", esta_vivo=lambda o: True)
        dono_do_underscore = claims.owner_of(r"file:C:\Oscar_Alho\arquivo.md", esta_vivo=lambda o: True)
        self.assertIsNotNone(dono_do_espaco)
        self.assertIsNotNone(dono_do_underscore)
        self.assertEqual(dono_do_espaco.owner.session_id, "sessao-a")
        self.assertEqual(dono_do_underscore.owner.session_id, "sessao-b")

    # ------------------------------------------------------------------
    # Achado 3 (auditoria 11/09): owner de tipo errado -> AttributeError cru
    # ------------------------------------------------------------------

    def _grava_claim_bruto(self, resource, payload):
        claims_dir = os.path.join(self._tmp, "claims")
        os.makedirs(claims_dir, exist_ok=True)
        fpath = claims._claim_file(resource)
        with open(fpath, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return fpath

    def test_owner_malformado_nao_levanta_attributeerror(self):
        "@finding-3 owner=null/string/lista (JSON valido, tipo errado) nunca pode virar AttributeError cru"
        resource = "browser:chrome.exe"
        for owner_malformado in (None, "nao-e-dict", [1, 2, 3], 42):
            with self.subTest(owner=owner_malformado):
                self._grava_claim_bruto(
                    resource,
                    {
                        "resource": resource,
                        "path": resource,
                        "range": None,
                        "owner": owner_malformado,
                        "scope": "turn",
                        "purpose": "",
                        "acquired_at": 1,
                        "renewed_at": 1,
                        "ttl_s": 900,
                    },
                )
                # nenhuma das tres chamadas pode levantar excecao
                self.assertTrue(claims.claim_ilegivel(resource), "deveria ser ilegivel, nao levantar")
                self.assertIsNone(claims.owner_of(resource))
                self.assertEqual(claims.overlapping(resource, None), [])

    def test_claim_top_level_nao_dict_nao_levanta_attributeerror(self):
        "@finding-3 o JSON do topo (nao so o owner) tambem pode nao ser dict"
        resource = "file:topo_ruim.txt"
        claims_dir = os.path.join(self._tmp, "claims")
        os.makedirs(claims_dir, exist_ok=True)
        fpath = claims._claim_file(resource)
        for conteudo_top_level in ("null", '"so uma string"', "[1, 2, 3]", "42"):
            with self.subTest(conteudo=conteudo_top_level):
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write(conteudo_top_level)
                self.assertTrue(claims.claim_ilegivel(resource))
                self.assertIsNone(claims.owner_of(resource))

    # ------------------------------------------------------------------
    # Achado 4 (auditoria 11/09): remove() incondicional apos leitura obsoleta
    # ------------------------------------------------------------------

    def test_remove_se_ainda_e_o_mesmo_nao_apaga_claim_trocado(self):
        "@finding-4 _remove_se_ainda_e_o_mesmo nao remove se o dono mudou entre a leitura e agora"
        resource = "file:alvo_race.txt"
        dono_velho = _owner("sessao-s1", pid=os.getpid())
        r1 = claims.claim(
            resource, dono_velho, 900, {"path": r"C:\dev\alvo_race.txt"}, esta_vivo=lambda o: True
        )
        self.assertTrue(r1.ok)
        claim_antigo = r1.claim  # snapshot que uma decisao de remocao teria usado
        fpath = claims._claim_file(resource)

        # simula: entre a leitura que decidiu remover e o remove() de fato,
        # uma OUTRA sessao ja roubou legitimamente o claim (via claim() de
        # producao, sem nenhum mock).
        os.remove(fpath)
        dono_novo = _owner("sessao-s3", pid=os.getpid() + 1)
        r2 = claims.claim(
            resource, dono_novo, 900, {"path": r"C:\dev\alvo_race.txt"}, esta_vivo=lambda o: True
        )
        self.assertTrue(r2.ok)

        removeu = claims._remove_se_ainda_e_o_mesmo(fpath, claim_antigo)
        self.assertFalse(removeu, "nao deveria remover: o arquivo ja pertence a outra sessao")

        dono_atual = claims.owner_of(resource, esta_vivo=lambda o: True)
        self.assertIsNotNone(dono_atual, "o claim fresco da sessao-s3 nao pode ter sumido")
        self.assertEqual(dono_atual.owner.session_id, "sessao-s3")

    def test_remove_se_ainda_e_o_mesmo_remove_quando_bate(self):
        "_remove_se_ainda_e_o_mesmo remove normalmente quando nada mudou (caminho feliz)"
        resource = "file:alvo_ok.txt"
        dono = _owner("sessao-unica", pid=os.getpid())
        r1 = claims.claim(resource, dono, 900, {"path": r"C:\dev\alvo_ok.txt"}, esta_vivo=lambda o: True)
        self.assertTrue(r1.ok)
        fpath = claims._claim_file(resource)

        removeu = claims._remove_se_ainda_e_o_mesmo(fpath, r1.claim)
        self.assertTrue(removeu)
        self.assertIsNone(claims.owner_of(resource, esta_vivo=lambda o: True))

    def test_release_nao_apaga_claim_roubado_no_meio_da_varredura(self):
        "@finding-4 release() nao pode apagar um claim fresco que outra sessao roubou entre a leitura e o remove"
        resource = "file:race_release.txt"
        dono_s1 = _owner("sessao-s1", pid=os.getpid())
        # ttl_s=-1: expira imediatamente, sem precisar esperar de verdade
        r1 = claims.claim(
            resource, dono_s1, -1, {"path": r"C:\dev\race_release.txt"}, esta_vivo=lambda o: True
        )
        self.assertTrue(r1.ok)

        original_read = claims._read_claim_file
        chamadas = {"n": 0}

        def read_com_corrida(fpath):
            resultado = original_read(fpath)
            chamadas["n"] += 1
            if chamadas["n"] == 1 and resultado is not None and resultado.resource == resource:
                # simula a corrida: entre a leitura que release() vai usar
                # para decidir remover e o os.remove() dele, a sessao-s3
                # rouba de verdade (claims.claim() de producao) o mesmo
                # recurso expirado de sessao-s1.
                dono_s3 = _owner("sessao-s3", pid=os.getpid() + 1)
                r_steal = claims.claim(
                    resource,
                    dono_s3,
                    999999,
                    {"path": r"C:\dev\race_release.txt"},
                    esta_vivo=lambda o: o.session_id != "sessao-s1",
                )
                self.assertTrue(r_steal.ok, "sessao-s3 devia conseguir roubar o claim expirado de s1")
            return resultado

        claims._read_claim_file = read_com_corrida
        try:
            removidos = claims.release(dono_s1, scope="all")
        finally:
            claims._read_claim_file = original_read

        dono_final = claims.owner_of(resource, esta_vivo=lambda o: True)
        self.assertIsNotNone(dono_final, "release() nao pode ter apagado o claim fresco da sessao-s3")
        self.assertEqual(dono_final.owner.session_id, "sessao-s3")
        self.assertEqual(removidos, 0, "nao houve remocao valida: o recurso ja era de sessao-s3 no momento do remove")


if __name__ == "__main__":
    unittest.main()
