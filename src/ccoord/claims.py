"""ccoord.claims - claims advisory (aviso, nao exclusao) com TTL e varredura.

Design: .specs/coordenacao-multissessao/design.md secoes 3.2, 4, 5 e 6.

Modelo mental
-------------
A unidade de coordenacao e (arquivo, faixa de linha) - nao o arquivo inteiro
(D-02). `claim()` e uma trava atomica por CHAVE DE RECURSO (`resource`, uma
string opaca escolhida por quem chama - normalmente `classify.py`, fora deste
modulo). `overlapping()` e uma consulta de leitura, independente, que cruza
`path` + `range` de TODOS os claims gravados: e o jeito de descobrir colisao
entre duas chaves de recurso DIFERENTES que apontam para o mesmo arquivo (ex.:
duas faixas de linha distintas). Por isso `claim()` nao nega por sobreposicao
de faixa - isso e recado do `policy.py` (fora de escopo aqui); claims aqui sao
ADVISORY, nao exclusao.

Primitiva de exclusao: `os.open(caminho, O_CREAT|O_EXCL|O_WRONLY)`.
PROIBIDO tmp+rename - no Windows `os.rename()` da EPERM quando outro processo
tem handle aberto no destino (RNF-06; foi a raiz do cluster de corrupcao do
~/.claude.json).

Dono morto e detectado por PID + tempo de criacao do processo (D-06), nunca
por PID sozinho (PID sofre reuso). Toda funcao publica aceita `esta_vivo`
por injecao - o modulo `ccoord.sessions` (outra task, em paralelo) fornecera
a implementacao real depois; aqui existe so um default proprio, sem importar
`ccoord.sessions`.

Raiz de estado: variavel de ambiente CCOORD_HOME (default `~/.claude/coord`).
Nenhuma escrita acontece fora dela.

Custo de import (T-017, RNF-04): `ctypes` (kernel32, so usado por
`_win_creation_ticks`) e importado TARDIAMENTE, dentro da funcao - so paga por
ele quem realmente disputa um claim ja existente (`_stealable`, chamado depois
de um `FileExistsError` real). O caso comum - `os.open(O_CREAT|O_EXCL)` livre
na primeira tentativa - nunca toca ctypes. `Owner`/`Claim`/`ClaimResult` sao
classes simples (nao `@dataclass`): o decorator arrasta `inspect` (~10ms de
import so para checar assinatura), custo fixo pago em todo hook antes desta
mudanca.
"""

from __future__ import annotations

import json
import os
import time
# `Callable` (usado so em anotacoes abaixo) nao e importado: com
# `from __future__ import annotations` as anotacoes viram string e nunca sao
# avaliadas em runtime - importar `typing` so para isso custaria ~2ms de
# import a toa no caminho quente. `Callable[[Owner], bool]` == a assinatura
# esperada de `esta_vivo`.

__all__ = [
    "Owner",
    "Claim",
    "ClaimResult",
    "claim",
    "owner_of",
    "overlapping",
    "release",
    "sweep",
    "esta_vivo_padrao",
]

_MAX_TENTATIVAS_DISPUTA = 8


# ---------------------------------------------------------------------------
# Raiz de estado e caminhos
# ---------------------------------------------------------------------------


def _home() -> str:
    return os.environ.get("CCOORD_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "coord"
    )


def _claims_dir() -> str:
    return os.path.join(_home(), "claims")


def _events_log_path() -> str:
    return os.path.join(_home(), "events.log")


def estado_legivel() -> bool:
    """O registro de claims esta utilizavel? (`False` = fail-closed no kill.)

    Existe porque `owner_of()` degrada em silencio: estado corrompido devolve
    `None`, indistinguivel de "recurso livre" -- e para o ramo de KILL essa
    confusao e justamente o caso perigoso (RNF-03/AC-010: nao poder ler o
    registro tem de RECUSAR o kill, porque nao matar e sempre seguro e o dano
    do kill e irreversivel).

    Achado por auditoria adversarial em 11/09: o AC-010 tinha teste passando
    injetando `estado_ilegivel=True` na politica, mas NENHUM entrypoint
    calculava a flag -- com `CCOORD_HOME` apontando para um arquivo, um
    `taskkill` real saia liberado.

    Barato de proposito: o caminho quente nao pode pagar por isto.

    CONSULTA PURA: nao cria nada. A primeira versao fazia `os.makedirs(...,
    exist_ok=True)` "para garantir", e isso vazou -- rodar qualquer teste sem
    `CCOORD_HOME` no ambiente criava `~/.claude/coord/claims/` no estado REAL da
    maquina (visto em 12/09). Funcao com nome de pergunta nao pode ter efeito
    colateral de escrita; quem precisa do diretorio e `claim()`, que ja o cria
    no caminho de escrita.
    """
    d = _claims_dir()
    try:
        if os.path.isdir(d):
            os.listdir(d)  # existe: da para ler?
            return True
        if os.path.exists(d):
            # existe e NAO e diretorio (claims/ virou um arquivo): inutilizavel.
            # Sem esta linha o codigo subia para o pai -- que e um diretorio
            # valido -- e devolvia True, liberando o kill.
            return False
        # Ainda nao existe (primeira execucao legitima com CCOORD_HOME novo):
        # "utilizavel" = daria para criar. Sobe ate o PRIMEIRO ancestral que
        # existe e pergunta se ele e diretorio.
        #   - CCOORD_HOME = um arquivo  -> o ancestral existente E o arquivo,
        #     nao e diretorio -> False (fail-closed, que e o caso do AC-010)
        #   - CCOORD_HOME = pasta nova em local valido -> ancestral e diretorio
        #     -> True (estado vazio nao e estado ilegivel)
        # Subir DOIS niveis de uma vez, como a primeira versao fazia, pulava
        # justamente o arquivo intruso e devolvia True -- o teste do AC-010
        # reprovou na hora.
        atual = os.path.dirname(d) or "."
        visitados = 0
        while not os.path.exists(atual) and visitados < 40:
            pai = os.path.dirname(atual)
            if not pai or pai == atual:
                break
            atual = pai
            visitados += 1
        return os.path.isdir(atual)
    except OSError:
        return False


def _now_ms() -> int:
    return int(time.time() * 1000)


def _slug(resource: str) -> str:
    """Codifica `resource` num nome de arquivo seguro e SEM COLISOES.

    Tem que ser injetora: duas chaves `resource` DIFERENTES (dois arquivos
    reais e distintos no disco do usuario) nunca podem virar o MESMO arquivo
    de claim. A versao antiga substituia QUALQUER char fora de alnum/-._  por
    '_' -- mas '_' e um char PERMITIDO (passava cru), entao um espaco e um
    underscore literal na MESMA posicao produziam o mesmo slug: 'Oscar Alho'
    (a pasta real do vault) e 'Oscar_Alho' colidiam no msmo arquivo de claim,
    e o aviso de conflito citava um peer que nunca tinha tocado o arquivo real
    do outro. Achado por auditoria adversarial em 11/09, repro completo em
    scratchpad/grupos/claims.md achado 1.

    Correcao: '_' deixa de ser passthrough -- vira SEMPRE um escape de
    largura FIXA (7 chars: '_' + 6 hex do ord() do char, cobre todo o range
    unicode ate 0x10FFFF). Como todo token literal tem exatamente 1 char e
    NUNCA e '_', e todo token de escape comeca com '_' e tem exatamente 7
    chars, dá para decodificar sem ambiguidade varrendo da esquerda pra
    direita (nao precisamos decodificar de verdade -- so saber que da,
    que e o que prova que dois `resource` diferentes nunca produzem o
    mesmo slug).
    """
    partes = []
    for c in resource:
        if c != "_" and (c.isalnum() or c in "-."):
            partes.append(c)
        else:
            partes.append("_%06x" % ord(c))
    seguro = "".join(partes)
    return seguro or "resource"


def _claim_file(resource: str) -> str:
    return os.path.join(_claims_dir(), _slug(resource) + ".json")


# ---------------------------------------------------------------------------
# Modelos de dados (design secao 4)
# ---------------------------------------------------------------------------


# Classes simples (nao @dataclass) por custo de import - ver docstring do
# modulo. "frozen" nao e reforcado em runtime (nenhum teste/codigo depende de
# imutabilidade real, hash ou __eq__ estrutural - so de atributos e dos
# metodos to_dict/from_dict abaixo, que reproduzem o mesmo contrato).


class Owner:
    """Identidade de quem detem um claim. PID+proc_start e o par forte de liveness."""

    def __init__(
        self,
        session_id: str,
        pid: int,
        proc_start: str,
        name: str = "",
        agent_id: str | None = None,
        pid_domain: str = "",
    ) -> None:
        self.session_id = session_id
        self.pid = pid
        self.proc_start = proc_start
        self.name = name
        self.agent_id = agent_id
        self.pid_domain = pid_domain

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "pid": self.pid,
            "proc_start": self.proc_start,
            "name": self.name,
            "agent_id": self.agent_id,
            "pid_domain": self.pid_domain,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Owner":
        if not isinstance(d, dict):
            # achado 3 (auditoria 11/09): owner=null/string/lista no JSON e
            # sintaticamente valido mas nao e um dict -- sem esta checagem,
            # `d.get(...)` levanta AttributeError (None/str/list nao tem
            # .get), que NAO estava no tuple de excecoes de
            # `_read_claim_file` e subia crua ate o hookio, que a engolia
            # como allow -- inclusive no kill (fail-closed furado). TypeError
            # ja e capturado por quem le claims (_read_claim_file); levantar
            # aqui em vez de deixar o AttributeError cru torna o defeito
            # explicito e ja coberto pelo except existente.
            raise TypeError(f"owner deve ser dict, recebi {type(d).__name__}")
        return cls(
            session_id=str(d.get("session_id", "")),
            pid=int(d.get("pid", 0)),
            proc_start=str(d.get("proc_start", "")),
            name=str(d.get("name", "")),
            agent_id=d.get("agent_id"),
            pid_domain=str(d.get("pid_domain", "")),
        )


class Claim:
    def __init__(
        self,
        resource: str,
        path: str,
        range: tuple[int, int] | None,  # None = arquivo inteiro
        owner: Owner,
        scope: str,  # "turn" | "session"
        purpose: str,
        acquired_at: int,
        renewed_at: int,
        ttl_s: int,
    ) -> None:
        self.resource = resource
        self.path = path
        self.range = range
        self.owner = owner
        self.scope = scope
        self.purpose = purpose
        self.acquired_at = acquired_at
        self.renewed_at = renewed_at
        self.ttl_s = ttl_s

    def to_dict(self) -> dict:
        return {
            "resource": self.resource,
            "path": self.path,
            "range": list(self.range) if self.range else None,
            "owner": self.owner.to_dict(),
            "scope": self.scope,
            "purpose": self.purpose,
            "acquired_at": self.acquired_at,
            "renewed_at": self.renewed_at,
            "ttl_s": self.ttl_s,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        if not isinstance(d, dict):
            # mesma classe do achado 3: JSON valido no topo mas nao-dict
            # (lista/string/numero) tambem quebrava em `d.get(...)` com
            # AttributeError cru antes desta checagem.
            raise TypeError(f"claim deve ser dict, recebi {type(d).__name__}")
        faixa = d.get("range")
        return cls(
            resource=d["resource"],
            path=str(d.get("path", d["resource"])),
            range=tuple(faixa) if faixa else None,
            owner=Owner.from_dict(d["owner"]),
            scope=str(d.get("scope", "turn")),
            purpose=str(d.get("purpose", "")),
            acquired_at=int(d.get("acquired_at", 0)),
            renewed_at=int(d.get("renewed_at", 0)),
            ttl_s=int(d.get("ttl_s", 0)),
        )


class ClaimResult:
    def __init__(self, ok: bool, claim: Claim | None, reason: str) -> None:
        self.ok = ok
        self.claim = claim
        self.reason = reason


# ---------------------------------------------------------------------------
# Liveness padrao: PID + tempo de criacao (Windows), nunca so PID (D-06)
# ---------------------------------------------------------------------------


def _win_creation_ticks(pid: int) -> int | None:
    """FILETIME (100ns ticks) de criacao do processo, ou None se nao existe/nao consulta.

    `ctypes` e importado AQUI DENTRO (RNF-04/T-017): esta funcao (via
    `esta_vivo_padrao`) so roda quando `claim()` encontra um claim JA
    EXISTENTE para o recurso disputado (`_stealable`) - o caso comum
    (`os.open(O_CREAT|O_EXCL)` livre de primeira) nunca chega aqui.
    """
    if pid is None or pid <= 0:
        return None
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None  # nao e Windows
    kernel32 = windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        from ctypes import wintypes

        criacao = wintypes.FILETIME()
        saida = wintypes.FILETIME()
        kernel_t = wintypes.FILETIME()
        user_t = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(criacao),
            ctypes.byref(saida),
            ctypes.byref(kernel_t),
            ctypes.byref(user_t),
        )
        if not ok:
            return None
        return (criacao.dwHighDateTime << 32) | criacao.dwLowDateTime
    except OSError:
        return None
    finally:
        kernel32.CloseHandle(handle)


def esta_vivo_padrao(owner: Owner) -> bool:
    """Default de liveness: PID existe E seu tempo de criacao bate com `proc_start`.

    PID sozinho sofre reuso (D-06) - por isso a comparacao de `proc_start` e
    obrigatoria quando disponivel. Ambiguidade (nao consegue consultar) resolve
    para "vivo" - fail-safe contra roubo indevido de um claim ativo; quem quer
    precisao real usa a injecao `esta_vivo` (ex.: `ccoord.sessions`, outra task).
    """
    ticks = _win_creation_ticks(owner.pid)
    if ticks is None:
        return False  # processo nao existe (ou nao e consultavel) -> morto
    if not owner.proc_start:
        return True  # sem dado para comparar -> nao arrisca roubo
    try:
        armazenado = int(owner.proc_start)
    except (TypeError, ValueError):
        return True
    return ticks == armazenado


# ---------------------------------------------------------------------------
# Regras puras: sobreposicao de faixa, expiracao, identidade de dono
# ---------------------------------------------------------------------------


def _ranges_overlap(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    """range None = arquivo inteiro, colide com qualquer faixa (inclusive outro None)."""
    if a is None or b is None:
        return True
    return a[0] <= b[1] and b[0] <= a[1]


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _same_owner_identity(a: Owner, b: Owner, *, casar_agent: bool) -> bool:
    if a.session_id != b.session_id:
        return False
    if casar_agent and b.agent_id is not None:
        return a.agent_id == b.agent_id
    return True


def _is_expired(c: Claim, agora_ms: int | None = None) -> bool:
    agora_ms = _now_ms() if agora_ms is None else agora_ms
    return (agora_ms - c.renewed_at) > (c.ttl_s * 1000)


def _stealable(c: Claim, esta_vivo: Callable[[Owner], bool]) -> bool:
    return _is_expired(c) or not esta_vivo(c.owner)


# ---------------------------------------------------------------------------
# IO defensivo
# ---------------------------------------------------------------------------


def _ensure_home() -> bool:
    try:
        os.makedirs(_claims_dir(), exist_ok=True)
        return True
    except OSError:
        return False


def _read_claim_file(fpath: str) -> Claim | None:
    try:
        with open(fpath, "r", encoding="utf-8") as fh:
            dados = json.load(fh)
        return Claim.from_dict(dados)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # leitura defensiva (ASM-003): arquivo truncado/corrompido nunca derruba
        # o conjunto - so este claim vira invisivel por uma leitura.
        # AttributeError entrou aqui pelo achado 3 (11/09): mesmo com as
        # checagens de isinstance em Owner.from_dict/Claim.from_dict (que ja
        # cobrem o caso conhecido, levantando TypeError, ja capturado acima),
        # esta funcao existe para NUNCA propagar excecao de dado malformado -
        # e uma 2a camada deliberada, nao redundante: sessions.py usa o mesmo
        # padrao de except amplo + checagem de tipo.
        return None


def _iter_claim_files() -> list[str]:
    d = _claims_dir()
    try:
        nomes = os.listdir(d)
    except OSError:
        return []
    return [os.path.join(d, n) for n in nomes if n.endswith(".json")]


def _log_event(kind: str, **campos) -> None:
    """Append-only, uma linha JSON por evento. Best-effort: nunca derruba o turno."""
    try:
        _ensure_home()
        payload = {"ts": _now_ms(), "event": kind, **campos}
        linha = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(_events_log_path(), os.O_CREAT | os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, linha)
        finally:
            os.close(fd)
    except OSError:
        pass


def _remove_se_ainda_e_o_mesmo(fpath: str, esperado: Claim) -> bool:
    """Reconfere o dono no arquivo IMEDIATAMENTE antes de remover.

    Achado 4 (auditoria 11/09): os tres pontos de escrita destrutiva do
    modulo (roubo em `claim()`, `release()`, `sweep()`) decidiam remover com
    base numa LEITURA anterior e chamavam `os.remove(fpath)` incondicional,
    sem reconferir o dono atual. Janela real: sessao S1 deixa um claim
    expirar e chama `release()`/SessionEnd; entre a leitura de release() e o
    `os.remove()`, a sessao S3 ja rouba legitimamente o mesmo recurso
    expirado via `claim()` (corrida real, nao hipotetica - repro completo em
    scratchpad/race_release.py). O release() de S1 apagava o claim FRESCO de
    S3, uma sessao totalmente alheia ao SessionEnd de S1.

    Nao e atomico de verdade - ainda sobra uma janela entre a releitura aqui
    e o `os.remove()` logo abaixo (o modulo so tem `O_CREAT|O_EXCL` como
    primitiva exclusiva; RNF-06 proibe tmp+rename no Windows e nao ha lock
    entre processos aqui). Mas encolhe a janela de "o tempo de uma varredura
    inteira do diretorio de claims" para "poucas instrucoes", que e a
    mitigacao praticavel sem introduzir lock novo. Compara identidade
    completa (session_id + pid + proc_start + acquired_at), nao so
    session_id: dono igual com timestamp diferente (ex.: renovou entre a
    leitura e agora) tambem NAO deve ser removido por quem decidiu com base
    no snapshot antigo.
    """
    atual = _read_claim_file(fpath)
    if atual is None:
        return False  # ja sumiu ou ficou ilegivel - nao ha o que remover
    if (
        atual.owner.session_id != esperado.owner.session_id
        or atual.owner.pid != esperado.owner.pid
        or atual.owner.proc_start != esperado.owner.proc_start
        or atual.acquired_at != esperado.acquired_at
    ):
        return False  # trocou de dono/renovou entre a decisao e agora: NAO mexer
    try:
        os.remove(fpath)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def claim(
    resource: str,
    owner: Owner,
    ttl_s: int,
    meta: dict | None = None,
    *,
    esta_vivo: Callable[[Owner], bool] | None = None,
) -> ClaimResult:
    """Adquire um claim advisory pela CHAVE `resource`.

    Se ja houver dono vivo, devolve o dono em vez de tomar (`ok=False`).
    Se o dono estiver morto ou o claim tiver expirado, rouba e loga
    `steal_stale`. Mesmo dono reclamando a mesma chave dentro do TTL apenas
    renova (`renewed_at` avanca, `acquired_at` preservado).

    `meta` carrega o que nao faz parte da assinatura fixa: `path` (default =
    `resource`), `range` (`tuple[int,int] | None`), `scope` (`"turn"|"session"`,
    default `"turn"`), `purpose`.
    """
    esta_vivo = esta_vivo or esta_vivo_padrao
    meta = meta or {}
    path = str(meta.get("path", resource))
    faixa = meta.get("range")
    faixa = tuple(faixa) if faixa else None
    scope = str(meta.get("scope", "turn"))
    purpose = str(meta.get("purpose", ""))

    if not _ensure_home():
        # fail-open (RNF-02): raiz de estado inacessivel nao trava o turno.
        _log_event("error", resource=resource, reason="home_indisponivel")
        virtual = Claim(resource, path, faixa, owner, scope, purpose, _now_ms(), _now_ms(), ttl_s)
        return ClaimResult(True, virtual, "fail_open:home_indisponivel")

    fpath = _claim_file(resource)

    for _ in range(_MAX_TENTATIVAS_DISPUTA):
        agora = _now_ms()
        payload = {
            "resource": resource,
            "path": path,
            "range": list(faixa) if faixa else None,
            "owner": owner.to_dict(),
            "scope": scope,
            "purpose": purpose,
            "acquired_at": agora,
            "renewed_at": agora,
            "ttl_s": ttl_s,
        }
        dados = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        try:
            fd = os.open(fpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, dados)
            finally:
                os.close(fd)
            novo = Claim.from_dict(payload)
            _log_event(
                "acquire",
                resource=resource,
                path=path,
                range=payload["range"],
                owner=payload["owner"],
                scope=scope,
            )
            return ClaimResult(True, novo, "acquired")
        except FileExistsError:
            pass
        except OSError as exc:
            _log_event("error", resource=resource, reason=str(exc))
            virtual = Claim(resource, path, faixa, owner, scope, purpose, agora, agora, ttl_s)
            return ClaimResult(True, virtual, "fail_open:os_error")

        existente = _read_claim_file(fpath)
        if existente is None:
            # arquivo la mas ilegivel/corrompido: trata como ausente, tenta
            # remover para reabrir espaco e recriar no proximo laco.
            try:
                os.remove(fpath)
            except OSError:
                pass
            continue

        if _same_owner_identity(existente.owner, owner, casar_agent=True) and not _is_expired(
            existente
        ):
            renovado = _renovar(fpath, existente, ttl_s, purpose or existente.purpose)
            if renovado is not None:
                _log_event(
                    "acquire",
                    resource=resource,
                    path=path,
                    range=payload["range"],
                    owner=owner.to_dict(),
                    scope=scope,
                    renewed=True,
                )
                return ClaimResult(True, renovado, "renewed")
            continue  # sumiu entre leitura e renovacao - disputa de novo

        if _stealable(existente, esta_vivo):
            # achado 4: so loga/considera roubado se REALMENTE removeu agora
            # (reconferido por _remove_se_ainda_e_o_mesmo). Se outra sessao ja
            # tiver trocado o dono entre a leitura acima e agora, nao
            # removemos nem logamos um roubo que nao aconteceu - o `continue`
            # abaixo faz o proximo laco reabrir com O_CREAT|O_EXCL, que ve o
            # dono novo de verdade e decide corretamente a partir dele.
            if _remove_se_ainda_e_o_mesmo(fpath, existente):
                _log_event(
                    "steal_stale",
                    resource=resource,
                    path=existente.path,
                    range=list(existente.range) if existente.range else None,
                    previous_owner=existente.owner.to_dict(),
                    new_owner=owner.to_dict(),
                )
            continue  # tenta criar de novo

        _log_event(
            "deny",
            resource=resource,
            path=path,
            range=payload["range"],
            owner=owner.to_dict(),
            held_by=existente.owner.to_dict(),
        )
        return ClaimResult(False, existente, "held_by_peer")

    # Disputa concorrente esgotou as tentativas (roubo simultaneo raro).
    existente = _read_claim_file(fpath)
    return ClaimResult(False, existente, "contention_exhausted")


def _renovar(fpath: str, existente: Claim, ttl_s: int, purpose: str) -> Claim | None:
    agora = _now_ms()
    atualizado = Claim(
        resource=existente.resource,
        path=existente.path,
        range=existente.range,
        owner=existente.owner,
        scope=existente.scope,
        purpose=purpose,
        acquired_at=existente.acquired_at,
        renewed_at=agora,
        ttl_s=ttl_s,
    )
    dados = json.dumps(atualizado.to_dict(), ensure_ascii=False).encode("utf-8")
    try:
        fd = os.open(fpath, os.O_WRONLY | os.O_TRUNC)
    except OSError:
        return None
    try:
        os.write(fd, dados)
    finally:
        os.close(fd)
    return atualizado


def claim_ilegivel(resource: str) -> bool:
    """O arquivo de claim DESTE recurso existe mas nao pode ser lido?

    `owner_of()` devolve `None` tanto para "livre" quanto para "arquivo
    corrompido" -- degradacao correta para avisar, PERIGOSA para o ramo de
    kill: um claim ilegivel de um recurso com dono VIVO passaria por livre e o
    kill sairia liberado.

    Achado pela 2a auditoria adversarial (11/09), reproduzido ponta a ponta:
    `estado_legivel()` auditava so o DIRETORIO, entao um `.json` corrompido do
    recurso especifico furava o fail-closed. Complementar, nao substituir:
    diretorio ilegivel e claim ilegivel sao falhas diferentes.
    """
    fpath = _claim_file(resource)
    try:
        if not os.path.isfile(fpath):
            return False  # nao existe = livre de verdade, nao e ilegibilidade
    except OSError:
        return True
    return _read_claim_file(fpath) is None


def owner_of(resource: str, *, esta_vivo: Callable[[Owner], bool] | None = None) -> Claim | None:
    """Dono atual do recurso, ou None se livre (inclui dono morto/expirado)."""
    esta_vivo = esta_vivo or esta_vivo_padrao
    existente = _read_claim_file(_claim_file(resource))
    if existente is None:
        return None
    if _stealable(existente, esta_vivo):
        return None
    return existente


def overlapping(
    path: str,
    lines: tuple[int, int] | None = None,
    *,
    esta_vivo: Callable[[Owner], bool] | None = None,
) -> list[Claim]:
    """Claims cujo (path, faixa) colide com (path, lines).

    `lines=None` (arquivo inteiro) colide com qualquer faixa registrada, e
    vice-versa. Duas faixas disjuntas no mesmo arquivo NAO colidem - e o caso
    que funcionou na pratica (relato da peer `home`) e nao pode virar recusa.

    Por padrao devolve todos os claims registrados que colidem, vivos ou nao
    (e uma consulta estrutural pura). Passe `esta_vivo` para filtrar so os
    vivos quando isso importar para quem chama.
    """
    achados: list[Claim] = []
    for fpath in _iter_claim_files():
        c = _read_claim_file(fpath)
        if c is None:
            continue
        if not _same_path(c.path, path):
            continue
        if not _ranges_overlap(c.range, lines):
            continue
        if esta_vivo is not None and _stealable(c, esta_vivo):
            continue
        achados.append(c)
    return achados


def release(owner: Owner, scope: str) -> int:
    """Libera claims do `owner` conforme `scope` e devolve quantos saíram.

    - ``"turn"``: só claims com `claim.scope == "turn"` do mesmo dono (casando
      `agent_id` quando informado) - uso típico no fim de turno (`Stop`).
    - ``"session"``: todos os claims (qualquer `claim.scope`) do mesmo dono,
      casando `agent_id` quando informado.
    - ``"all"``: todos os claims da sessão (`session_id`), ignorando
      `agent_id` - varredura total, uso típico em `SessionEnd` (AC-008): "não
      sobra lease nenhuma daquela sessão" vale para qualquer agente/subagente
      que a tenha criado.
    """
    if scope not in ("turn", "session", "all"):
        raise ValueError(f"scope invalido: {scope!r}")

    removidos = 0
    for fpath in _iter_claim_files():
        c = _read_claim_file(fpath)
        if c is None:
            continue

        if scope == "turn":
            bate = c.scope == "turn" and _same_owner_identity(c.owner, owner, casar_agent=True)
        elif scope == "session":
            bate = _same_owner_identity(c.owner, owner, casar_agent=True)
        else:  # "all"
            bate = c.owner.session_id == owner.session_id

        if not bate:
            continue

        # achado 4: reconfere o dono ANTES de apagar - `c` pode ser um
        # snapshot obsoleto (outra sessao ja roubou este mesmo recurso
        # expirado entre esta leitura e agora). Se o dono mudou, este
        # release() nao apaga mais - o claim novo nao e problema seu.
        if not _remove_se_ainda_e_o_mesmo(fpath, c):
            continue
        removidos += 1
        _log_event(
            "release",
            resource=c.resource,
            path=c.path,
            range=list(c.range) if c.range else None,
            owner=c.owner.to_dict(),
            scope=scope,
        )
    return removidos


def sweep(*, esta_vivo: Callable[[Owner], bool] | None = None) -> int:
    """Remove claims de dono morto/expirado; cada remocao vira `steal_stale`."""
    esta_vivo = esta_vivo or esta_vivo_padrao
    removidos = 0
    for fpath in _iter_claim_files():
        c = _read_claim_file(fpath)
        if c is None:
            continue
        if not _stealable(c, esta_vivo):
            continue
        # achado 4: mesma reconferencia de release()/claim() - so conta e
        # loga se o snapshot `c` ainda bate com o arquivo na hora do remove.
        if not _remove_se_ainda_e_o_mesmo(fpath, c):
            continue
        removidos += 1
        _log_event(
            "steal_stale",
            resource=c.resource,
            path=c.path,
            range=list(c.range) if c.range else None,
            previous_owner=c.owner.to_dict(),
            new_owner=None,
        )
    return removidos
