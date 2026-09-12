"""ccoord.sessions - descoberta de sessoes vivas do Claude Code, SOMENTE LEITURA.

Fonte unica de verdade: ``~/.claude/sessions/<pid>.json`` (mantido pelo harness).
Este modulo nao escreve nada nesse diretorio nem em qualquer outro lugar.

Regras que vieram de erro real (ver .specs/coordenacao-multissessao/design.md 3.1/6):

1. ``status`` e ROTULO, nao decisao. O vocabulario tem pelo menos "busy", "idle" e
   "waiting", e o campo pode estar minutos desatualizado enquanto outra sessao
   atualiza a cada ~40s. Nada aqui compara ``status == "idle"`` para decidir liveness.
2. Liveness forte = PID existe **e** o ``procStart`` do processo real casa com o do
   registro (PID sozinho sofre reuso). A consulta ao SO nunca propaga excecao: se
   falhar por qualquer motivo, degrada para "PID existe" + ``updatedAt`` dentro de
   um TTL (600s por padrao).
3. Leitura defensiva por ARQUIVO: um JSON truncado ou em escrita parcial nunca
   derruba a listagem inteira.
4. Sessoes de outro ``pidDomain`` (ex.: WSL x Windows nativo) sao ignoradas em
   ``peers()`` - nao se alcancam. Registro SEM o campo (ausente/None) conta
   como dominio diferente tambem - fail-closed (achado #2 da 4a auditoria,
   11/09: antes, ``pidDomain`` ausente era tratado como "mesmo host").
5. ``_iter_session_files()`` tem um TETO (``_MAX_SESSION_FILES``) em quantos
   arquivos abre por chamada, priorizando os de mtime mais recente. Nada
   neste modulo apaga ``~/.claude/sessions/*.json`` (regra do projeto: SOMENTE
   LEITURA), entao o diretorio so cresce; sem teto, ``peers()`` teria custo
   proporcional ao total ja acumulado, nao ao numero de sessoes vivas (achado
   #1 da 4a auditoria, 11/09: 500 arquivos = 6-7s nesta maquina).

Custo de import (T-017, RNF-04): este modulo roda dentro de um subprocesso NOVO
a cada hook - o custo de IMPORTAR (nao so de executar) entra no orcamento de
150ms. Por isso `ctypes`/`ctypes.wintypes` (consulta ao kernel32, so usada por
`is_alive()`/`peers()`) e `socket` (so usado por `_local_pid_domain()`, tambem
so chamado a partir de `peers()`) sao importados TARDIAMENTE, dentro das
funcoes que de fato os usam - nao no topo do modulo. `pathlib.Path` e mantido
como o TIPO PUBLICO de retorno de `sessions_dir()` (contrato de
`tests/test_sessions.py` e de `ccoord/cli.py`, que chama `.exists()` nele), mas
o caminho internamente usado por `me()`/`peers()` e uma STRING simples
(`_sessions_dir_str()`) - import de `pathlib` so acontece quando alguem chama
`sessions_dir()` de verdade, nao em toda leitura de sessao. `dataclasses` foi
trocado por uma classe simples (`Session.__init__`): o decorator @dataclass
importa `inspect` (que arrasta `dis`/`tokenize`/`ast`) so para checar
assinatura - ~10ms so de import, pagos em TODO hook antes desta mudanca,
mesmo quando nao ha peer nenhuma para checar.
"""

from __future__ import annotations

import json
import os
import sys
import time

DEFAULT_TTL_S = 600

# Teto duro de quantos arquivos _iter_session_files()/peers() abrem por
# chamada (achado #1 da 4a auditoria, 11/09). Este modulo nunca apaga
# ~/.claude/sessions/*.json (SOMENTE LEITURA - regra do projeto), entao o
# diretorio pode so crescer ao longo de semanas se sessoes travarem/forem
# mortas sem o harness limpar o registro. Medido: 500 arquivos acumulados
# custam 6-7s so no open()/read() (_read_session_file), 40-50x o orcamento de
# 150ms do RNF-04 - o gargalo e o Windows Defender escaneando cada JSON na
# primeira leitura, nao o syscall de liveness (esse sozinho: 15ms p/ 500).
# Acima do teto, prioriza os arquivos de mtime mais recente (via
# `DirEntry.stat()`, que no Windows usa o WIN32_FIND_DATA ja trazido pelo
# proprio scandir - sem open() extra) porque o harness reescreve o registro
# de uma sessao viva a cada ~40s (docstring do modulo): um mtime velho e
# sinal forte de sessao morta, entao cortar os mais antigos primeiro e a
# escolha que menos arrisca esconder uma peer de fato viva.
_MAX_SESSION_FILES = 64

# Sentinelas do resultado da consulta ao SO (Windows). Distintos de "None" para
# nao confundir "processo nao existe" com "nao consegui perguntar".
_NO_SUCH_PROCESS = object()
_QUERY_UNAVAILABLE = object()

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
# ERROR_ACCESS_DENIED: o processo existe, so nao podemos inspecionar (peer
# elevada ou de outro usuario). Nunca confundir com "nao existe".
_ERROR_ACCESS_DENIED = 5

# handle cacheado do kernel32 (ver comentario em _query_process_creation_ticks)
_KERNEL32 = None


class Session:
    """Um registro de ~/.claude/sessions/<pid>.json, ja tipado.

    Classe simples (nao @dataclass) por custo de import (ver docstring do
    modulo): mesmos campos/defaults de antes, sem trazer `dataclasses`
    (que arrasta `inspect`) para dentro do caminho quente.
    """

    def __init__(
        self,
        pid: int | None = None,
        session_id: str | None = None,
        cwd: str | None = None,
        name: str | None = None,
        status: str | None = None,
        updated_at: int | None = None,
        started_at: int | None = None,
        proc_start: str | None = None,
        pid_domain: str | None = None,
        version: str | None = None,
        messaging_socket_path: str | None = None,
        peer_features: list | None = None,
        kind: str | None = None,
        raw: dict | None = None,
    ) -> None:
        self.pid = pid
        self.session_id = session_id
        self.cwd = cwd
        self.name = name
        self.status = status
        self.updated_at = updated_at
        self.started_at = started_at
        self.proc_start = proc_start
        self.pid_domain = pid_domain
        self.version = version
        self.messaging_socket_path = messaging_socket_path
        # default_factory=list equivalente: uma lista NOVA por instancia,
        # nunca uma lista mutavel compartilhada entre Sessions.
        self.peer_features = list(peer_features) if peer_features is not None else []
        self.kind = kind
        self.raw = raw if raw is not None else {}


def _sessions_dir_str() -> str:
    """Igual a `sessions_dir()`, mas devolve `str` - sem tocar em `pathlib`.

    Uso interno (`me()`/`peers()`/`_iter_session_files()`): evita importar
    `pathlib` (que arrasta `glob`/`posixpath`/`fnmatch` no import, ~4-5ms) no
    caminho quente, que so precisa juntar nomes de arquivo, nunca dos metodos
    de `Path`. Equivalente comprovado: `os.path.expanduser("~")` e
    `str(Path.home())` resolvem para a mesma string nesta maquina/versao.
    """
    override = os.environ.get("CCOORD_SESSIONS_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", "sessions")


def sessions_dir():
    """Diretorio de registros de sessao, como `pathlib.Path` (API publica).

    Respeita CCOORD_SESSIONS_DIR (e o que torna os testes possiveis sem tocar em
    ~/.claude). Sem a variavel, default e ~/.claude/sessions.

    `pathlib` e importado AQUI DENTRO, tardiamente (RNF-04/T-017): esta funcao
    e usada por testes e por `ccoord/cli.py` (que chama `.exists()` no
    retorno) - nao pelo caminho quente dos hooks, que usa `_sessions_dir_str()`
    internamente. Ver docstring do modulo.
    """
    from pathlib import Path

    return Path(_sessions_dir_str())


def _local_pid_domain() -> str:
    """Reproduz o formato medido: "win32:laptop-q3ai3ek1" (plataforma + hostname
    em minusculo).

    `socket` e importado tardiamente: so quem chama `peers()` paga por ele -
    `me()` (sempre chamado) nao precisa de pidDomain local nenhum.
    """
    try:
        import socket

        host = socket.gethostname().lower()
    except Exception:
        host = ""
    return f"{sys.platform}:{host}"


def _parse_session(data: dict) -> Session:
    return Session(
        pid=data.get("pid"),
        session_id=data.get("sessionId"),
        cwd=data.get("cwd"),
        name=data.get("name"),
        status=data.get("status"),
        updated_at=data.get("updatedAt"),
        started_at=data.get("startedAt"),
        proc_start=data.get("procStart"),
        pid_domain=data.get("pidDomain"),
        version=data.get("version"),
        messaging_socket_path=data.get("messagingSocketPath"),
        peer_features=list(data.get("peerFeatures") or []),
        kind=data.get("kind"),
        raw=data,
    )


def _read_session_file(path) -> Session | None:
    """Le um arquivo de registro. Nunca levanta: arquivo truncado, JSON invalido,
    permissao negada ou o que for -> None, e a listagem segue com os demais.

    `path` pode ser `str` ou `pathlib.Path` (os testes passam Path) - `open()`
    aceita os dois (`os.PathLike`), entao nao ha necessidade de importar
    `pathlib` so para ler este arquivo.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        return _parse_session(data)
    except Exception:
        return None


def _mtime_ou_zero(entry) -> float:
    """`entry.stat().st_mtime`, ou 0.0 se o arquivo sumiu entre o scandir e o
    stat (janela de corrida normal com escrita concorrente) - nunca levanta.
    """
    try:
        return entry.stat().st_mtime
    except OSError:
        return 0.0


def _iter_session_files() -> list[str]:
    d = _sessions_dir_str()
    try:
        with os.scandir(d) as it:
            entradas = [e for e in it if e.name.endswith(".json")]
    except OSError:
        return []
    if len(entradas) > _MAX_SESSION_FILES:
        # Acima do teto: so paga o stat() (cache do scandir no Windows, sem
        # open() extra) para escolher os mais recentes - ver _MAX_SESSION_FILES.
        entradas.sort(key=_mtime_ou_zero, reverse=True)
        del entradas[_MAX_SESSION_FILES:]
    else:
        entradas.sort(key=lambda e: e.name)
    return [e.path for e in entradas]


def _iter_sessions() -> list[Session]:
    result = []
    for path in _iter_session_files():
        s = _read_session_file(path)
        if s is not None:
            result.append(s)
    return result


def _proc_start_as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _query_process_creation_ticks(pid: int):
    """Retorna o FILETIME (ticks de 100ns desde 1601) de criacao do processo real,
    via kernel32 (stdlib, sem dependencia externa).

    Retorno:
      int                -> processo existe, ticks de criacao lidos com sucesso.
      _NO_SUCH_PROCESS    -> OpenProcess nao encontrou o PID (resposta valida).
      _QUERY_UNAVAILABLE  -> handle abriu mas GetProcessTimes falhou (existencia
                             confirmada, ticks indisponiveis).

    Nunca levanta: qualquer excecao inesperada e capturada por quem chama
    (is_alive), que degrada para o caminho de TTL.

    `ctypes`/`ctypes.wintypes` sao importados AQUI DENTRO (RNF-04/T-017): esta
    funcao so roda quando `is_alive()`/`peers()` de fato precisam checar uma
    sessao (o caminho lento) - a maioria dos hooks nunca chega aqui (ver
    `hooks/coord_pre_write.py`/`coord_pre_bash.py`, que so chamam
    `sessions.peers()` quando ja ha um claim de conflito em jogo).
    """
    if sys.platform != "win32":
        return _QUERY_UNAVAILABLE

    import ctypes
    import ctypes.wintypes as wintypes

    # CACHEADO no modulo: `ctypes.WinDLL("kernel32", ...)` recarrega a DLL a cada
    # chamada e isso e caminho quente -- na primeira versao desta correcao o gate
    # de p95 < 150 ms reprovou em duas rodadas seguidas (regressao minha, pega
    # pelo gate). `ctypes.windll.kernel32` seria cacheado, mas nao expoe
    # `use_last_error`, que e justamente o que distingue ACCESS_DENIED de
    # "processo inexistente".
    global _KERNEL32
    if _KERNEL32 is None:
        _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = _KERNEL32
    ctypes.set_last_error(0)
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # ACCESS_DENIED (5) significa que o processo EXISTE e nao temos direito de
        # inspecionar -- peer rodando elevada, ou de outro usuario. Tratar isso
        # como "nao existe" era um defeito de gravidade alta (achado da 3a
        # auditoria, 11/09, reproduzido com o PID 4 = System): a peer sumiria da
        # lista de vivas, seus claims virariam "de dono morto" e passiveis de
        # roubo, e o kill contra ela sairia liberado. Aqui o unico desfecho
        # seguro e "nao sei" -- que degrada para os sinais fracos (TTL) em vez de
        # afirmar morte.
        erro = ctypes.get_last_error()
        if erro == _ERROR_ACCESS_DENIED:
            return _QUERY_UNAVAILABLE
        return _NO_SUCH_PROCESS
    try:
        # O kernel object do processo pode continuar referenciado (por um handle
        # que o processo-pai ainda segura, ex.: subprocess.Popen no Windows) mesmo
        # depois de terminado - OpenProcess/GetProcessTimes teriam sucesso com o
        # creation time antigo. GetExitCodeProcess e o jeito de distinguir "ainda
        # rodando" de "kernel object so nao foi liberado ainda".
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return _QUERY_UNAVAILABLE
        if exit_code.value != _STILL_ACTIVE:
            return _NO_SUCH_PROCESS

        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return _QUERY_UNAVAILABLE
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def _pid_exists(pid: int) -> bool:
    """Existencia "fraca" do PID, usada so no caminho degradado. Nunca levanta:
    se nao der pra confirmar, assume que existe (RNF-02, fail-open - este modulo
    e informativo, nao autoriza kill nenhum)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True  # handle abriu mas nao deu pra confirmar; fail-open
            return exit_code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return True


def _degraded_alive(s: Session, ttl_s: int, now_ms: int | None) -> bool:
    if not _pid_exists(s.pid):
        return False
    if s.updated_at is None:
        return False
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    try:
        age_ms = now_ms - int(s.updated_at)
    except (TypeError, ValueError):
        return False
    return 0 <= age_ms <= ttl_s * 1000


def is_alive(s: Session, ttl_s: int = DEFAULT_TTL_S, now_ms: int | None = None) -> bool:
    """PID existe e o procStart do processo real casa com o do registro (liveness
    forte). Se a consulta ao SO falhar por qualquer motivo, degrada para
    "PID existe" + updatedAt dentro do TTL. Nunca levanta.

    ``status`` do registro nunca entra nesta conta - e rotulo, nao decisao.
    """
    if s is None or s.pid is None:
        return False

    try:
        result = _query_process_creation_ticks(s.pid)
    except Exception:
        result = _QUERY_UNAVAILABLE

    if result is _NO_SUCH_PROCESS:
        return False

    if result is _QUERY_UNAVAILABLE:
        return _degraded_alive(s, ttl_s, now_ms)

    recorded = _proc_start_as_int(s.proc_start)
    if recorded is None:
        # registro sem procStart utilizavel: nao da pra provar o sinal forte,
        # degrada em vez de recusar liveness so por falta do campo.
        return _degraded_alive(s, ttl_s, now_ms)

    return result == recorded


def me(session_id: str | None = None) -> Session | None:
    """Identifica a sessao atual.

    Preferencia: as variaveis de ambiente que o hook recebe (medidas em
    medicao-hooks.md secao 2) - CLAUDE_PID e CLAUDE_CODE_SESSION_ID. Com o PID em
    mao, cai para o arquivo correspondente. Sem CLAUDE_PID no ambiente, usa o
    session_id (do ambiente ou do parametro) para procurar o registro cujo
    ``sessionId`` bate, entre todos os arquivos.
    """
    pid_str = os.environ.get("CLAUDE_PID")
    env_session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    sid = env_session_id or session_id

    pid: int | None = None
    if pid_str is not None:
        try:
            pid = int(pid_str)
        except ValueError:
            pid = None

    if pid is not None:
        # string simples (nao Path): `me()` roda em TODO hook, entao nao pode
        # pagar o import de `pathlib` so para montar um nome de arquivo.
        s = _read_session_file(os.path.join(_sessions_dir_str(), f"{pid}.json"))
        if s is not None:
            return s

    if sid is not None:
        for s in _iter_sessions():
            if s.session_id == sid:
                return s

    return None


def peers(exclude_pid: int | None = None) -> list[Session]:
    """Sessoes vivas, exceto a propria e as de outro pidDomain.

    ``exclude_pid`` default: PID da propria sessao (via ``me()``); se ``me()`` nao
    resolver (sem env, sem session_id), nada e excluido por PID.
    """
    if exclude_pid is None:
        my = me()
        exclude_pid = my.pid if my is not None else None

    local_domain = _local_pid_domain()
    result = []
    for s in _iter_sessions():
        if exclude_pid is not None and s.pid == exclude_pid:
            continue
        # pidDomain ausente (None) conta como dominio DIFERENTE, nao como
        # "mesmo host" - fail-closed. Achado #2 da 4a auditoria (11/09): o
        # docstring do modulo promete que sessao de outro subsistema nunca se
        # alcanca, e um registro sem o campo (formato antigo, escrita
        # parcial) e informacao insuficiente para afirmar que o PID gravado
        # tem QUALQUER relacao com um processo Windows real deste host - o
        # harness hoje sempre grava pidDomain (v2.1.261), entao isto so entra
        # em jogo no caso ja-degradado que o campo existe para cobrir.
        if local_domain and s.pid_domain != local_domain:
            continue
        if not is_alive(s):
            continue
        result.append(s)
    return result
