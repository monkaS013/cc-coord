"""Testes de ccoord.sessions - descoberta de sessoes vivas, somente leitura.

Todo teste roda contra um diretorio temporario (CCOORD_SESSIONS_DIR), nunca
contra ~/.claude real. A tag @spec:AC-xxx vai na primeira linha do docstring
(e o que o TAP/onp-spec usam como titulo do teste).
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
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from ccoord import sessions  # noqa: E402


def _write(dir_path: Path, pid: int, data: dict) -> Path:
    p = dir_path / f"{pid}.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _base_record(pid: int, **overrides) -> dict:
    rec = {
        "pid": pid,
        "sessionId": f"session-{pid}",
        "cwd": "C:\\Users\\usuario\\dev\\cc-coord",
        "startedAt": 1789157840941,
        "procStart": "134336314386097043",
        "version": "2.1.261",
        "peerProtocol": 1,
        "peerFeatures": ["notify_idle", "artifact_yield"],
        "kind": "interactive",
        "entrypoint": "cli",
        "pidDomain": sessions._local_pid_domain(),
        "messagingSocketPath": f"\\\\.\\pipe\\LOCAL\\cc-msg-{pid}",
        "name": f"peer-{pid}",
        "status": "idle",
        "updatedAt": int(time.time() * 1000),
    }
    rec.update(overrides)
    return rec


class SessionsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._old_sessions_dir = os.environ.get("CCOORD_SESSIONS_DIR")
        self._old_pid = os.environ.get("CLAUDE_PID")
        self._old_session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CCOORD_SESSIONS_DIR"] = str(self.dir)
        os.environ.pop("CLAUDE_PID", None)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def tearDown(self):
        self._tmp.cleanup()
        if self._old_sessions_dir is None:
            os.environ.pop("CCOORD_SESSIONS_DIR", None)
        else:
            os.environ["CCOORD_SESSIONS_DIR"] = self._old_sessions_dir
        if self._old_pid is None:
            os.environ.pop("CLAUDE_PID", None)
        else:
            os.environ["CLAUDE_PID"] = self._old_pid
        if self._old_session_id is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self._old_session_id

    # ------------------------------------------------------------------
    # sessions_dir()

    def test_sessions_dir_respeita_env(self):
        "@spec:AC-012 sessions_dir respeita CCOORD_SESSIONS_DIR"
        self.assertEqual(sessions.sessions_dir(), self.dir)

    def test_sessions_dir_default_sem_env(self):
        "@spec:AC-012 sessions_dir cai para ~/.claude/sessions sem a env var"
        os.environ.pop("CCOORD_SESSIONS_DIR", None)
        esperado = Path.home() / ".claude" / "sessions"
        self.assertEqual(sessions.sessions_dir(), esperado)

    # ------------------------------------------------------------------
    # leitura defensiva

    def test_arquivo_truncado_nao_derruba_listagem(self):
        "@spec:AC-012 arquivo JSON truncado no meio do diretorio nao derruba peers()"
        # processo real (o proprio teste) para o vizinho bom ficar vivo de verdade
        my_pid = os.getpid()
        ticks = sessions._query_process_creation_ticks(my_pid)
        self.assertIsInstance(ticks, int)
        _write(self.dir, my_pid, _base_record(my_pid, procStart=str(ticks)))
        # arquivo corrompido ao lado
        ruim = self.dir / "999999.json"
        ruim.write_text('{"pid": 999999, "sessionId": "quebrado", "cwd": "C:\\', encoding="utf-8")

        resultado = sessions.peers()

        self.assertTrue(any(s.pid == my_pid for s in resultado))

    def test_json_nao_e_objeto_e_ignorado(self):
        "@spec:AC-012 JSON valido mas que nao e objeto (ex.: lista) e ignorado"
        p = self.dir / "1.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(sessions._iter_sessions(), [])

    def test_ausencia_de_chaves_nao_quebra(self):
        "@spec:AC-012 registro com chaves faltando nao levanta excecao"
        _write(self.dir, 111, {"pid": 111})
        s = sessions._read_session_file(self.dir / "111.json")
        self.assertIsNotNone(s)
        self.assertEqual(s.pid, 111)
        self.assertIsNone(s.session_id)
        self.assertIsNone(s.proc_start)
        self.assertEqual(s.peer_features, [])
        # e nao deve quebrar is_alive nem peers()
        self.assertFalse(sessions.is_alive(s, now_ms=int(time.time() * 1000)))
        self.assertEqual(sessions.peers(), [])

    # ------------------------------------------------------------------
    # status e rotulo, nao decisao

    def test_status_idle_nao_exclui_sessao_viva(self):
        "@spec:AC-012 status idle nao e motivo para excluir uma sessao de fato viva"
        my_pid = os.getpid()
        ticks = sessions._query_process_creation_ticks(my_pid)
        _write(
            self.dir,
            my_pid,
            _base_record(my_pid, status="idle", procStart=str(ticks)),
        )
        resultado = sessions.peers(exclude_pid=-1)
        self.assertTrue(any(s.pid == my_pid and s.status == "idle" for s in resultado))

    def test_status_busy_desatualizado_nao_salva_sessao_morta(self):
        "@spec:AC-012 status busy velho nao encobre um PID que nao existe mais"
        pid_morto = 4_000_111  # improvavel de existir na maquina de teste
        registro = _base_record(
            pid_morto,
            status="busy",
            procStart="1",
            updatedAt=int(time.time() * 1000) - 400_000,  # 400s atras, dentro do TTL
        )
        _write(self.dir, pid_morto, registro)
        s = sessions._read_session_file(self.dir / f"{pid_morto}.json")
        # o PID nao existe -> mesmo com status "busy" recente-o-bastante, nao e vivo
        self.assertFalse(sessions.is_alive(s))
        self.assertEqual(sessions.peers(), [])

    # ------------------------------------------------------------------
    # pidDomain de outro host/subsistema

    def test_pid_domain_diferente_e_ignorado(self):
        "@spec:AC-012 pidDomain de outra maquina/subsistema (ex.: WSL) e ignorado"
        my_pid = os.getpid()
        ticks = sessions._query_process_creation_ticks(my_pid)
        _write(
            self.dir,
            my_pid,
            _base_record(my_pid, procStart=str(ticks), pidDomain="linux:algum-wsl"),
        )
        resultado = sessions.peers(exclude_pid=-1)
        self.assertEqual(resultado, [])

    def test_pid_domain_ausente_e_tratado_como_estrangeiro(self):
        "@spec:AC-012 registro sem o campo pidDomain (ausente) e excluido de peers(), nao tratado como mesmo host"
        my_pid = os.getpid()
        ticks = sessions._query_process_creation_ticks(my_pid)
        registro = _base_record(my_pid, procStart=str(ticks))
        del registro["pidDomain"]  # simula formato antigo/escrita parcial, campo ausente de verdade
        _write(self.dir, my_pid, registro)

        s = sessions._read_session_file(self.dir / f"{my_pid}.json")
        self.assertIsNone(s.pid_domain)
        # sem o campo, is_alive() sozinho ainda diria vivo (procStart bate) -
        # e peers() que precisa recusar por falta de dominio comprovado.
        self.assertTrue(sessions.is_alive(s))
        self.assertEqual(sessions.peers(exclude_pid=-1), [])

    # ------------------------------------------------------------------
    # PID reusado: mesmo pid, procStart diferente

    def test_pid_reusado_com_procstart_diferente_nao_e_vivo(self):
        "@spec:AC-012 PID reusado (mesmo pid, procStart real diferente) nao conta como vivo"
        my_pid = os.getpid()
        ticks_reais = sessions._query_process_creation_ticks(my_pid)
        self.assertIsInstance(ticks_reais, int)
        proc_start_falso = str(ticks_reais + 123456)
        _write(self.dir, my_pid, _base_record(my_pid, procStart=proc_start_falso))
        s = sessions._read_session_file(self.dir / f"{my_pid}.json")
        self.assertFalse(sessions.is_alive(s))
        self.assertEqual(sessions.peers(), [])

    def test_pid_reusado_liveness_forte_via_subprocess_real(self):
        "@spec:AC-012 processo filho real com procStart correto e vivo; encerrado, nao e"
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(20)"],
        )
        try:
            ticks = sessions._query_process_creation_ticks(proc.pid)
            self.assertIsInstance(ticks, int)
            _write(
                self.dir,
                proc.pid,
                _base_record(proc.pid, procStart=str(ticks)),
            )
            s = sessions._read_session_file(self.dir / f"{proc.pid}.json")
            self.assertTrue(sessions.is_alive(s))
        finally:
            proc.terminate()
            proc.wait(timeout=10)

        # apos encerrar, o PID nao existe mais (ou foi reaproveitado por outro
        # processo com procStart diferente) -> nao e mais vivo
        s2 = sessions._read_session_file(self.dir / f"{proc.pid}.json")
        self.assertFalse(sessions.is_alive(s2))

    # ------------------------------------------------------------------
    # is_alive: degradacao quando a consulta ao SO falha

    def test_is_alive_degrada_para_ttl_quando_query_falha(self):
        "@spec:AC-012 quando a consulta ao SO falha, degrada para PID+TTL sem levantar"
        my_pid = os.getpid()
        registro = _base_record(my_pid, procStart="not-a-number")
        s = sessions._parse_session(registro)

        original = sessions._query_process_creation_ticks
        sessions._query_process_creation_ticks = lambda pid: sessions._QUERY_UNAVAILABLE
        try:
            agora = int(time.time() * 1000)
            self.assertTrue(sessions.is_alive(s, now_ms=agora))
            # fora do TTL -> nao e mais vivo
            self.assertFalse(sessions.is_alive(s, ttl_s=1, now_ms=agora + 5000))
        finally:
            sessions._query_process_creation_ticks = original

    def test_is_alive_nunca_levanta_mesmo_se_query_quebra(self):
        "@spec:AC-012 excecao na consulta ao SO nunca propaga; is_alive so retorna bool"
        my_pid = os.getpid()
        s = sessions._parse_session(_base_record(my_pid))

        def _explode(pid):
            raise OSError("falha simulada de kernel32")

        original = sessions._query_process_creation_ticks
        sessions._query_process_creation_ticks = _explode
        try:
            resultado = sessions.is_alive(s)
            self.assertIsInstance(resultado, bool)
        finally:
            sessions._query_process_creation_ticks = original

    def test_is_alive_pid_inexistente_e_falso(self):
        "@spec:AC-012 PID que nao existe no SO nunca e vivo, mesmo dentro do TTL"
        pid_inexistente = 4_000_222
        s = sessions._parse_session(
            _base_record(pid_inexistente, updatedAt=int(time.time() * 1000))
        )
        self.assertFalse(sessions.is_alive(s))

    # ------------------------------------------------------------------
    # me()

    def test_me_usa_env_vars_para_achar_o_proprio_arquivo(self):
        "@spec:AC-012 me() prefere CLAUDE_PID/CLAUDE_CODE_SESSION_ID para achar o registro"
        my_pid = os.getpid()
        _write(self.dir, my_pid, _base_record(my_pid))
        os.environ["CLAUDE_PID"] = str(my_pid)
        os.environ["CLAUDE_CODE_SESSION_ID"] = f"session-{my_pid}"

        resultado = sessions.me()

        self.assertIsNotNone(resultado)
        self.assertEqual(resultado.pid, my_pid)
        self.assertEqual(resultado.session_id, f"session-{my_pid}")

    def test_me_cai_para_busca_por_session_id_sem_claude_pid(self):
        "@spec:AC-012 sem CLAUDE_PID, me() procura o arquivo pelo session_id"
        outro_pid = 555555
        _write(self.dir, outro_pid, _base_record(outro_pid, sessionId="minha-sessao"))

        resultado = sessions.me(session_id="minha-sessao")

        self.assertIsNotNone(resultado)
        self.assertEqual(resultado.pid, outro_pid)

    def test_me_retorna_none_sem_pid_e_sem_match(self):
        "@spec:AC-012 sem CLAUDE_PID e sem sessao correspondente, me() retorna None"
        self.assertIsNone(sessions.me())
        self.assertIsNone(sessions.me(session_id="nao-existe"))

    # ------------------------------------------------------------------
    # peers()

    def test_lista_so_as_vivas(self):
        "@spec:AC-012 o mapa lista as outras sessoes vivas com seus diretorios"
        my_pid = os.getpid()
        os.environ["CLAUDE_PID"] = str(my_pid)
        os.environ["CLAUDE_CODE_SESSION_ID"] = f"session-{my_pid}"
        _write(self.dir, my_pid, _base_record(my_pid))

        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
        try:
            ticks = sessions._query_process_creation_ticks(proc.pid)
            _write(
                self.dir,
                proc.pid,
                _base_record(proc.pid, procStart=str(ticks), cwd="C:\\outro\\dir"),
            )

            resultado = sessions.peers()

            pids = {s.pid for s in resultado}
            self.assertIn(proc.pid, pids)
            self.assertNotIn(my_pid, pids)
            peer = next(s for s in resultado if s.pid == proc.pid)
            self.assertEqual(peer.cwd, "C:\\outro\\dir")
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_iter_session_files_tem_teto_priorizando_mtime_recente(self):
        "@spec:AC-012 _iter_session_files() tem teto de arquivos e prioriza os de mtime mais recente"
        total = sessions._MAX_SESSION_FILES + 20
        agora = time.time()
        for i in range(total):
            pid = 700_000 + i
            caminho = _write(self.dir, pid, _base_record(pid, procStart="1"))
            # arquivo 0 e o mais velho; os demais ficam progressivamente mais recentes
            os.utime(caminho, (agora - (total - i), agora - (total - i)))

        # a sessao de fato viva (este processo) e a de mtime mais recente de todas
        my_pid = os.getpid()
        ticks = sessions._query_process_creation_ticks(my_pid)
        caminho_vivo = _write(self.dir, my_pid, _base_record(my_pid, procStart=str(ticks)))
        os.utime(caminho_vivo, (agora + 1000, agora + 1000))

        arquivos = sessions._iter_session_files()

        self.assertEqual(len(arquivos), sessions._MAX_SESSION_FILES)
        self.assertTrue(any(f"{my_pid}.json" in a for a in arquivos))
        # o mais velho do lote (pid 700000) ficou de fora do teto
        self.assertFalse(any("700000.json" in a for a in arquivos))

        resultado = sessions.peers(exclude_pid=-1)
        self.assertTrue(any(s.pid == my_pid for s in resultado))

    def test_peers_exclude_pid_explicito(self):
        "@spec:AC-012 exclude_pid explicito prevalece sobre a resolucao via me()"
        my_pid = os.getpid()
        ticks = sessions._query_process_creation_ticks(my_pid)
        _write(self.dir, my_pid, _base_record(my_pid, procStart=str(ticks)))

        resultado = sessions.peers(exclude_pid=my_pid)

        self.assertEqual(resultado, [])


if __name__ == "__main__":
    unittest.main()
