"""Testes de ccoord.cli - visibilidade sob demanda (RF-08), unittest stdlib.

Todo teste roda contra CCOORD_HOME e CCOORD_SESSIONS_DIR temporarios, nunca
contra ~/.claude real. A tag @spec:AC-xxx vai na primeira linha do docstring
(e o que o TAP/onp-spec usam como titulo do teste). Saida capturada via
contextlib.redirect_stdout e verificada pelo CONTEUDO, nao so pelo exit code.
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
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from ccoord import cli, claims, sessions  # noqa: E402


def _write_session(dir_path: Path, pid: int, **overrides) -> Path:
    rec = {
        "pid": pid,
        "sessionId": f"session-{pid}",
        "cwd": "C:\\Users\\ViniciusMoraisHDT\\dev\\cc-coord",
        "startedAt": 1789157840941,
        "procStart": "134336314386097043",
        "version": "2.1.261",
        "peerFeatures": ["notify_idle"],
        "kind": "interactive",
        "pidDomain": sessions._local_pid_domain(),
        "messagingSocketPath": f"\\\\.\\pipe\\LOCAL\\cc-msg-{pid}",
        "name": f"peer-{pid}",
        "status": "idle",
        "updatedAt": int(time.time() * 1000),
    }
    rec.update(overrides)
    p = dir_path / f"{pid}.json"
    p.write_text(json.dumps(rec), encoding="utf-8")
    return p


def _owner(session_id, pid=None, proc_start="", name="", agent_id=None):
    return claims.Owner(
        session_id=session_id,
        pid=pid if pid is not None else os.getpid(),
        proc_start=proc_start,
        name=name or session_id,
        agent_id=agent_id,
    )


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        codigo = cli.main(argv)
    return codigo, buf.getvalue()


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get("CCOORD_HOME")
        self._old_sessions_dir = os.environ.get("CCOORD_SESSIONS_DIR")
        self._old_pid = os.environ.get("CLAUDE_PID")
        self._old_session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
        self._old_agent_id = os.environ.get("CLAUDE_AGENT_ID")

        self._tmp = tempfile.mkdtemp(prefix="ccoord_cli_test_")
        self.home = os.path.join(self._tmp, "coord")
        self.sessions_dir = Path(self._tmp) / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        os.environ["CCOORD_HOME"] = self.home
        os.environ["CCOORD_SESSIONS_DIR"] = str(self.sessions_dir)
        os.environ.pop("CLAUDE_PID", None)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        os.environ.pop("CLAUDE_AGENT_ID", None)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

        def _restore(key, old):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

        _restore("CCOORD_HOME", self._old_home)
        _restore("CCOORD_SESSIONS_DIR", self._old_sessions_dir)
        _restore("CLAUDE_PID", self._old_pid)
        _restore("CLAUDE_CODE_SESSION_ID", self._old_session_id)
        _restore("CLAUDE_AGENT_ID", self._old_agent_id)

    # ------------------------------------------------------------------
    # status: sessoes vivas + recursos ocupados (AC-012)
    # ------------------------------------------------------------------

    def test_status_lista_sessoes_vivas_com_cwd_e_recursos_ocupados(self):
        "@spec:AC-012 status lista as sessoes vivas com seus diretorios e os recursos ocupados"
        my_pid = os.getpid()
        ticks = sessions._query_process_creation_ticks(my_pid)
        self.assertIsInstance(ticks, int)
        _write_session(
            self.sessions_dir,
            my_pid,
            procStart=str(ticks),
            name="minha-sessao-de-teste",
            cwd="C:\\dev\\cc-coord",
            status="busy",
        )

        dono = _owner(f"session-{my_pid}", pid=my_pid, proc_start=str(ticks), name="minha-sessao-de-teste")
        r = claims.claim(
            "file:app.js:100-200",
            dono,
            900,
            {"path": r"C:\dev\app.js", "range": (100, 200), "purpose": "ajuste da legenda"},
        )
        self.assertTrue(r.ok)

        codigo, saida = _run(["status"])

        self.assertEqual(codigo, 0)
        self.assertIn("SESSOES VIVAS", saida.upper())
        self.assertIn("minha-sessao-de-teste", saida)
        self.assertIn("C:\\dev\\cc-coord", saida)
        self.assertIn("RECURSOS OCUPADOS", saida.upper())
        self.assertIn("C:\\dev\\app.js", saida)
        self.assertIn("ajuste da legenda", saida)
        # regra 3: rotulo de status aparece, mas junto com "ha quanto tempo"
        self.assertIn("busy", saida)
        self.assertIn("Atualizado", saida)

    def test_status_json_traz_a_mesma_informacao_estruturada(self):
        "status --json devolve a mesma informacao em formato programatico"
        my_pid = os.getpid()
        ticks = sessions._query_process_creation_ticks(my_pid)
        _write_session(self.sessions_dir, my_pid, procStart=str(ticks), name="sessao-json")

        # achado MEDIA (auditoria 11/09): o unico teste de --json nunca criava
        # uma claim nem conferia `recursos_ocupados` -- a paridade com o modo
        # texto (que TEM cobertura, em
        # test_status_lista_sessoes_vivas_com_cwd_e_recursos_ocupados) era
        # promessa do docstring sem prova nenhuma no lado JSON. Reproduzido:
        # com `recursos_ocupados` mutado para `[]` fixo em cmd_status, a suite
        # inteira continuava verde. Corrigido criando uma claim e conferindo o
        # payload, igual ao que o teste de texto ja faz para a mesma info.
        dono = _owner(f"session-{my_pid}", pid=my_pid, proc_start=str(ticks), name="sessao-json")
        r = claims.claim(
            "file:app.js:100-200",
            dono,
            900,
            {"path": r"C:\dev\app.js", "range": (100, 200), "purpose": "ajuste da legenda"},
        )
        self.assertTrue(r.ok)

        codigo, saida = _run(["status", "--json"])
        self.assertEqual(codigo, 0)
        payload = json.loads(saida)
        self.assertTrue(payload["sessoes_dir_existe"])
        self.assertEqual(len(payload["sessoes"]), 1)
        self.assertEqual(payload["sessoes"][0]["nome"], "sessao-json")
        self.assertTrue(payload["claims_dir_existe"])
        self.assertEqual(len(payload["recursos_ocupados"]), 1)
        ocupado = payload["recursos_ocupados"][0]
        self.assertEqual(ocupado["path"], r"C:\dev\app.js")
        self.assertEqual(ocupado["range"], [100, 200])
        self.assertEqual(ocupado["proposito"], "ajuste da legenda")
        self.assertTrue(ocupado["ativo"])
        self.assertEqual(payload["recursos_pendentes_sweep"], [])

    # ------------------------------------------------------------------
    # who: recurso ocupado e recurso livre
    # ------------------------------------------------------------------

    def test_who_recurso_ocupado_mostra_o_dono(self):
        "who aponta o dono de um recurso ocupado"
        dono = _owner("sessao-dona", pid=os.getpid())
        claims.claim(
            "file:x.js:10-20",
            dono,
            900,
            {"path": r"C:\dev\x.js", "range": (10, 20), "purpose": "refactor"},
        )

        codigo, saida = _run(["who", r"C:\dev\x.js"])

        self.assertEqual(codigo, 0)
        self.assertIn("sessao-dona", saida)
        self.assertIn("refactor", saida)

    def test_who_recurso_livre(self):
        "who aponta livre quando ninguem detem o recurso"
        codigo, saida = _run(["who", r"C:\dev\nao_existe.js"])
        self.assertEqual(codigo, 0)
        self.assertIn("livre", saida.lower())

    # ------------------------------------------------------------------
    # release --mine
    # ------------------------------------------------------------------

    def test_release_mine_libera_claims_da_sessao_atual(self):
        "release --mine libera as claims desta sessao"
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sessao-cli-teste"
        os.environ["CLAUDE_PID"] = str(os.getpid())

        dono = _owner("sessao-cli-teste", pid=os.getpid(), name="sessao-cli-teste")
        claims.claim("file:meu.js", dono, 900, {"path": r"C:\dev\meu.js"})

        codigo, saida = _run(["release", "--mine"])

        self.assertEqual(codigo, 0)
        self.assertIn("sessao-cli-teste", saida)
        self.assertIsNone(claims.owner_of("file:meu.js", esta_vivo=lambda o: True))

    def test_release_mine_sem_identidade_nao_quebra(self):
        "release --mine sem conseguir identificar a sessao devolve mensagem, nao traceback"
        codigo, saida = _run(["release", "--mine"])
        self.assertEqual(codigo, 1)
        self.assertIn("não foi possível identificar", saida.lower())

    # ------------------------------------------------------------------
    # sweep
    # ------------------------------------------------------------------

    def test_sweep_remove_claims_de_dono_morto(self):
        "sweep remove claims de dono morto e diz quantas removeu"
        dono_morto = _owner("sessao-morta-cli", pid=4_000_333, proc_start="1")
        claims.claim("file:orfao_cli.js", dono_morto, 900, {"path": r"C:\dev\orfao_cli.js"})

        codigo, saida = _run(["sweep"])

        self.assertEqual(codigo, 0)
        self.assertIn("1", saida)
        self.assertIsNone(claims.owner_of("file:orfao_cli.js", esta_vivo=lambda o: True))

    def test_sweep_sem_claims_nao_quebra(self):
        "sweep sem nada para remover informa isso sem erro"
        codigo, saida = _run(["sweep"])
        self.assertEqual(codigo, 0)
        self.assertIn("nenhuma", saida.lower())

    # ------------------------------------------------------------------
    # regra 2: tres casos de degradacao - status NUNCA falha
    # ------------------------------------------------------------------

    def test_status_diretorio_de_estado_ausente(self):
        "@spec:AC-012 status com diretorio de estado ausente nunca quebra"
        # CCOORD_HOME aponta para um caminho que nunca foi criado (nem claims/
        # nem coord/ existem); CCOORD_SESSIONS_DIR tambem aponta para o vazio.
        os.environ["CCOORD_HOME"] = os.path.join(self._tmp, "nunca_existiu")
        vazio_sessions = Path(self._tmp) / "sessions_vazio_nao_criado"
        os.environ["CCOORD_SESSIONS_DIR"] = str(vazio_sessions)

        codigo, saida = _run(["status"])

        self.assertEqual(codigo, 0)
        self.assertIn("não encontrado", saida)
        self.assertIn("não inicializado", saida)

    def test_status_arquivo_de_claim_corrompido_nao_derruba_o_comando(self):
        "status com um arquivo de claim corrompido no meio continua funcionando"
        dono = _owner("sessao-boa", pid=os.getpid())
        claims.claim("file:bom.js", dono, 900, {"path": r"C:\dev\bom.js", "purpose": "ok"})

        claims_dir = os.path.join(self.home, "claims")
        os.makedirs(claims_dir, exist_ok=True)
        ruim = os.path.join(claims_dir, "_corrompido.json")
        with open(ruim, "w", encoding="utf-8") as fh:
            fh.write("{isso nao e json valido")

        codigo, saida = _run(["status"])

        self.assertEqual(codigo, 0)
        self.assertIn(r"C:\dev\bom.js", saida)
        self.assertNotIn("Traceback", saida)

    def test_status_nenhuma_sessao_viva(self):
        "@spec:AC-012 status com nenhuma sessao viva mostra mensagem propria, nao lista vazia muda"
        # sessions_dir existe mas esta vazio
        codigo, saida = _run(["status"])
        self.assertEqual(codigo, 0)
        self.assertIn("Nenhuma sessão viva", saida)

    # ------------------------------------------------------------------
    # achado ALTA (auditoria 11/09): `ccoord status`/`who` escalavam
    # linearmente com o numero de claims em disco (500 claims sinteticos ->
    # 7,3s). Instrumentado antes de corrigir: a causa real medida NAO era
    # `esta_vivo_padrao` (os claims do repro original ja nasciam expirados --
    # `acquired_at`/`renewed_at` no passado, `ttl_s` pequeno -- entao o
    # curto-circuito `_is_expired(...) or esta_vivo(...)` em cmd_status nunca
    # chegava a chamar esta_vivo); era o custo da PRIMEIRA leitura de cada
    # `.json` novo em `claims/` (~8-15ms medido isolando I/O puro nesta
    # maquina, sem nenhum codigo do ccoord -- 2a leitura do MESMO arquivo caiu
    # para ~0.1ms, perfil classico de antivirus escaneando abertura de
    # arquivo). Os 3 testes abaixo prova cada parte da correcao separadamente,
    # com `time.sleep` monkeypatchado (deterministico, nao depende do
    # antivirus real da maquina que roda o teste nem produz falso negativo em
    # CI sem esse perfil de I/O).
    # ------------------------------------------------------------------

    def test_status_le_arquivos_de_claim_em_paralelo(self):
        "@spec:AC-012 status le os arquivos de claim em paralelo, nao em serie (achado ALTA 11/09)"
        n = 20
        atraso_s = 0.03
        claims_dir = os.path.join(self.home, "claims")
        os.makedirs(claims_dir, exist_ok=True)
        for i in range(n):
            with open(os.path.join(claims_dir, f"c_{i}.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")  # conteudo irrelevante: _read_claim_file e mockado abaixo

        dono = _owner("sessao-leitura-paralela")
        claim_valido = claims.Claim(
            resource="file:x.js",
            path=r"C:\dev\x.js",
            range=None,
            owner=dono,
            scope="turn",
            purpose="",
            acquired_at=int(time.time() * 1000),
            renewed_at=int(time.time() * 1000),
            ttl_s=900,
        )

        def leitura_lenta(path):
            time.sleep(atraso_s)
            return claim_valido

        with mock.patch.object(cli, "_read_claim_file", side_effect=leitura_lenta):
            t0 = time.perf_counter()
            codigo, saida = _run(["status", "--json"])
            dt = time.perf_counter() - t0

        self.assertEqual(codigo, 0)
        limite_serial = n * atraso_s  # 0.6s se lesse 1 arquivo de cada vez
        self.assertLess(
            dt,
            limite_serial * 0.5,
            f"status levou {dt:.3f}s para ler {n} claims (serial seria "
            f"~{limite_serial:.3f}s) -- esperava leitura em paralelo",
        )

    def test_make_esta_vivo_memoiza_por_identidade_de_dono(self):
        "status nao repete OpenProcess para claims da MESMA sessao morta (achado ALTA 11/09)"
        dono_morto = _owner("sessao-morta-repetida", pid=4_000_555, proc_start="1")
        for i in range(5):
            claims.claim(
                f"file:repetido_{i}.js", dono_morto, 900, {"path": rf"C:\dev\repetido_{i}.js"}
            )

        chamadas: list[str] = []
        original = claims.esta_vivo_padrao

        def contador(owner):
            chamadas.append(owner.session_id)
            return original(owner)

        with mock.patch.object(claims, "esta_vivo_padrao", side_effect=contador):
            codigo, saida = _run(["status", "--json"])

        self.assertEqual(codigo, 0)
        self.assertEqual(
            len(chamadas), 1, f"esperava 1 chamada memoizada (5 claims, 1 dono), teve {chamadas}"
        )

    def test_status_resolve_donos_distintos_em_paralelo(self):
        "status resolve varios donos DISTINTOS de sessao desconhecida em paralelo (achado ALTA 11/09)"
        n = 12
        atraso_s = 0.05
        for i in range(n):
            dono = _owner(f"sessao-fantasma-{i}", pid=5_000_000 + i, proc_start="0")
            claims.claim(f"file:fantasma_{i}.js", dono, 900, {"path": rf"C:\dev\fantasma_{i}.js"})

        def lento(owner):
            time.sleep(atraso_s)
            return False  # todos mortos

        with mock.patch.object(claims, "esta_vivo_padrao", side_effect=lento):
            t0 = time.perf_counter()
            codigo, saida = _run(["status", "--json"])
            dt = time.perf_counter() - t0

        self.assertEqual(codigo, 0)
        limite_serial = n * atraso_s  # 0.6s se resolvesse 1 dono de cada vez
        self.assertLess(
            dt,
            limite_serial * 0.5,
            f"status levou {dt:.3f}s para {n} donos distintos (serial seria "
            f"~{limite_serial:.3f}s) -- esperava paralelismo real",
        )


if __name__ == "__main__":
    unittest.main()
