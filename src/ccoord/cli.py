"""ccoord.cli - visibilidade sob demanda (RF-08).

`ccoord status | who <recurso> | release --mine | sweep`.

Existe para o Vinicius ler no terminal, nao para maquina consumir -- por isso
a saida padrao e texto alinhado em colunas, em portugues; `--json` cobre o uso
programatico. So stdlib (RNF-01).

Regra que nao pode ser quebrada (design.md secao 5, RNF-02): este comando
NUNCA falha com traceback. Diretorio de estado ausente, arquivo de claim
corrompido, nenhuma sessao viva -- cada um tem saida humana propria; o `main()`
ainda envolve a execucao inteira num guarda-chuva de excecao, por seguranca.

Sobre `ccoord/claims.py` e `ccoord/sessions.py`: este modulo so usa a API
publica delas. A leitura direta de `<CCOORD_HOME>/claims/*.json` abaixo nao
reimplementa logica interna -- e o schema documentado em design.md secoes 4 e
6 (raiz por `CCOORD_HOME`, um arquivo json por claim). O modulo `claims.py` nao
expõe "listar todos os claims", entao o CLI precisa ler o diretorio para
montar o mapa; a leitura e defensiva (por arquivo, nunca derruba o conjunto),
igual ao padrao usado dentro de `claims.py`.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from typing import Callable

from ccoord import claims, sessions

__all__ = ["main"]

# Achado ALTA (auditoria 11/09): teto de threads para paralelizar leitura de
# claims (_all_claims) e resolucao de liveness de donos desconhecidos
# (_prefetch_esta_vivo_padrao). Nao e caminho quente de hook (RF-08: CLI sob
# demanda), entao o import acima nao paga o orcamento de 150ms dos hooks -- so
# custa quando o Vinicius roda `ccoord status/who/sweep` no terminal.
_MAX_PARALLEL_WORKERS = 16


def _print_json(obj: dict, *, indent: int | None = None) -> None:
    """Imprime JSON do `--json` em ASCII puro. Ponto UNICO de saida JSON do CLI.

    Mesmo motivo do `hookio._imprimir` (T-035), e este era o residuo que sobrou
    daquele conserto: quando a saida do CLI vai para um PIPE -- que e o caso de
    todo consumo programatico, `ccoord status --json | ...` --, o
    `sys.stdout.encoding` desta maquina e **cp1252**, nao UTF-8. Com
    `ensure_ascii=False` os acentos viram bytes cp1252 e o JSON deixa de ser
    UTF-8 valido; quem fizer `json.loads(saida.decode("utf-8"))` recebe
    `UnicodeDecodeError`. E os dados do CLI TEM acento: caminho do vault
    ("Inteligencia de Mercado"), "Area de Trabalho", nome de sessao.

    Com `ensure_ascii=True` o conteudo e identico apos o parse (escapes
    `\\uXXXX` sao a mesma string), e a saida passa a ser valida em qualquer
    encoding. Funcao unica de proposito para que o proximo comando `--json`
    nao repita a escolha -- foram CINCO ocorrencias espalhadas quando isto foi
    corrigido.
    """
    print(json.dumps(obj, ensure_ascii=True, indent=indent))


# ---------------------------------------------------------------------------
# Leitura do estado de claims (contrato documentado, nao API privada)
# ---------------------------------------------------------------------------


def _ccoord_home() -> str:
    return os.environ.get("CCOORD_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "coord"
    )


def _claims_dir() -> str:
    return os.path.join(_ccoord_home(), "claims")


def _claims_dir_exists() -> bool:
    return os.path.isdir(_claims_dir())


def _read_claim_file(path: str) -> claims.Claim | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            dados = json.load(fh)
        return claims.Claim.from_dict(dados)
    except Exception:
        # leitura defensiva: arquivo truncado/corrompido nunca quebra o
        # comando, so fica invisivel nesta leitura (mesma postura de claims.py).
        return None


def _all_claims() -> list[claims.Claim]:
    d = _claims_dir()
    try:
        nomes = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    except OSError:
        return []
    if not nomes:
        return []
    caminhos = [os.path.join(d, n) for n in nomes]
    if len(caminhos) == 1:
        c = _read_claim_file(caminhos[0])
        return [c] if c is not None else []

    # Achado ALTA (auditoria 11/09), causa raiz REAL medida por instrumentacao
    # (nao a que o achado apontou -- ver _prefetch_esta_vivo_padrao para o
    # detalhe): a primeira leitura de cada arquivo .json novo em `claims/`
    # custava ~8-15ms nesta maquina (medido isolando I/O puro, sem nenhum
    # codigo do ccoord -- 2a leitura do MESMO arquivo caiu para ~0.1ms, o
    # perfil classico de antivirus escaneando abertura de arquivo). Com
    # centenas de claims acumulados (nenhum hook varre `claims/` sozinho -- ver
    # comentario em cmd_status), isso sozinho ja explicava os 7,3s medidos
    # para 500 claims, MESMO SEM nenhuma chamada a esta_vivo_padrao (os claims
    # do repro ja nasciam expirados, entao o `or` de curto-circuito em
    # cmd_status nunca chegava a chamar esta_vivo). `open()`/leitura de arquivo
    # libera o GIL, entao paralelizar aqui e ganho real (medido: 657ms -> 40ms
    # para 80 arquivos frescos, ~16x com 16 threads) -- nao so cosmetico.
    workers = min(_MAX_PARALLEL_WORKERS, len(caminhos))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        # `pool.map` devolve na MESMA ordem de `caminhos` (ordem alfabetica de
        # nome de arquivo), so a execucao interna e concorrente -- comportamento
        # observavel identico ao loop sequencial anterior, so mais rapido.
        lidos = pool.map(_read_claim_file, caminhos)
    return [c for c in lidos if c is not None]


def _is_expired(c: claims.Claim, agora_ms: int) -> bool:
    return (agora_ms - c.renewed_at) > (c.ttl_s * 1000)


# ---------------------------------------------------------------------------
# Liveness: cruza com o registro real de sessoes quando disponivel (o proprio
# claims.esta_vivo_padrao convida a essa injecao mais precisa), com fallback
# para o sinal de processo puro.
# ---------------------------------------------------------------------------


def _session_index() -> dict[str, sessions.Session]:
    # exclude_pid=-1: nenhum PID real e -1, entao nada e excluido por "ser eu
    # mesmo" -- queremos TODAS as sessoes vivas, inclusive a que roda o CLI.
    return {s.session_id: s for s in sessions.peers(exclude_pid=-1) if s.session_id}


def _make_esta_vivo(
    indice: dict[str, sessions.Session],
    *,
    cache: dict[tuple[str, int, str], bool] | None = None,
) -> Callable[[claims.Owner], bool]:
    # `cache` memoiza por IDENTIDADE DE DONO (session_id, pid, proc_start), nao
    # por claim -- achado ALTA (auditoria 11/09): sem isto, cada claim de um
    # dono fora do indice de peers vivas pagava seu PROPRIO
    # OpenProcess+GetProcessTimes real (`claims.esta_vivo_padrao`), mesmo
    # quando varias claims mortas vinham da MESMA sessao encerrada (o caso mais
    # comum na pratica: uma sessao que morre costuma deter varias claims ao
    # mesmo tempo). `cache` pode vir pre-aquecido por
    # `_prefetch_esta_vivo_padrao` (cmd_status), que resolve donos DISTINTOS em
    # paralelo -- ver comentario la para o caso de N donos todos diferentes.
    cache = {} if cache is None else cache

    def esta_vivo(owner: claims.Owner) -> bool:
        if owner.session_id in indice:
            return True  # ja filtrado por sessions.peers() == vivo de verdade
        # sessao fora do registro atual (encerrada, ou fora do pidDomain):
        # cai para o sinal de processo do proprio claims.py.
        chave = (owner.session_id, owner.pid, owner.proc_start)
        if chave not in cache:
            cache[chave] = claims.esta_vivo_padrao(owner)
        return cache[chave]

    return esta_vivo


def _prefetch_esta_vivo_padrao(
    owners: list[claims.Owner],
) -> dict[tuple[str, int, str], bool]:
    """Resolve `claims.esta_vivo_padrao()` para varios `owners` em PARALELO.

    Achado ALTA (auditoria 11/09). IMPORTANTE sobre o repro original (500
    claims sinteticas -> 7,3s): instrumentado antes de corrigir, a causa
    daquele numero especifico NAO era esta funcao/`esta_vivo_padrao` -- os
    claims do repro ja nasciam EXPIRADOS (`acquired_at`/`renewed_at` no
    passado, `ttl_s` pequeno), entao o curto-circuito `_is_expired(...) or
    esta_vivo(...)` em cmd_status nunca chegava a chamar `esta_vivo_padrao`; a
    causa real medida foi a leitura dos arquivos (ver comentario em
    `_all_claims`, corrigido la). O que esta funcao resolve e um cenario
    IRMAO, real e nao coberto pela correcao de `_all_claims`: N claims NAO
    expiradas (dentro do TTL) de donos DISTINTOS cujo `session_id` nao esta no
    indice de peers vivas -- por exemplo, sessoes derrubadas por `taskkill`
    minutos atras, ainda dentro do TTL, cada uma com um claim proprio. Sem
    isto, cada uma pagaria seu PROPRIO OpenProcess+GetProcessTimes sequencial
    (`claims.esta_vivo_padrao`); provado por teste com delay monkeypatchado
    (`test_status_resolve_donos_distintos_em_paralelo`, falha sem esta funcao).

    A chamada ao kernel32 dentro de `esta_vivo_padrao` e via ctypes, que libera
    o GIL durante a chamada de funcao C externa -- por isso threads aqui
    produzem paralelismo de verdade (nao so cosmetico), sem precisar tocar em
    `claims.py` (fora do grupo `cli`).

    Dedupe (`_make_esta_vivo`) e esta funcao sao COMPLEMENTARES: dedupe ajuda
    quando claims mortas compartilham o MESMO dono (o caso mais comum: uma
    sessao morta costuma deter varias claims); esta funcao ajuda quando os
    donos sao DISTINTOS. O fundo do problema mais amplo -- nada varre
    `claims/` sozinho, entao claims acumulam indefinidamente (confirmado por
    grep: nenhum dos 7 hooks chama `claims.sweep()`) -- exigiria sweep
    automatico num hook (`coord_session_start.py` ja tem o comentario, mas e
    arquivo de outro grupo): relatado como dependencia, nao corrigido aqui.
    """
    unicos: dict[tuple[str, int, str], claims.Owner] = {}
    for o in owners:
        unicos.setdefault((o.session_id, o.pid, o.proc_start), o)

    if not unicos:
        return {}
    if len(unicos) == 1:
        # 1 dono so: thread pool so acrescentaria overhead de criacao de
        # thread sem nada para sobrepor.
        ((chave, dono),) = unicos.items()
        return {chave: claims.esta_vivo_padrao(dono)}

    resultado: dict[tuple[str, int, str], bool] = {}
    workers = min(_MAX_PARALLEL_WORKERS, len(unicos))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futuros = {
            pool.submit(claims.esta_vivo_padrao, dono): chave
            for chave, dono in unicos.items()
        }
        for fut in concurrent.futures.as_completed(futuros):
            chave = futuros[fut]
            try:
                resultado[chave] = fut.result()
            except Exception:
                # regra 2 (nunca traceback) + mesma postura fail-safe de
                # esta_vivo_padrao: ambiguidade nunca vira roubo de claim ativo.
                resultado[chave] = True
    return resultado


def _current_owner() -> claims.Owner | None:
    """Identidade desta sessao/agente para `release --mine`.

    Preferencia: `sessions.me()` (mesma resolucao usada em todo o resto do
    projeto). Sem registro de sessao (CLI rodado fora de um hook, sem arquivo
    em ~/.claude/sessions), cai para as variaveis de ambiente cruas -- o
    minimo que `release()` precisa e o `session_id` (a comparacao de dono nao
    depende de `proc_start`).
    """
    s = sessions.me()
    if s is not None and s.session_id:
        return claims.Owner(
            session_id=s.session_id,
            pid=s.pid or 0,
            proc_start=s.proc_start or "",
            name=s.name or "",
            agent_id=os.environ.get("CLAUDE_AGENT_ID") or None,
            pid_domain=s.pid_domain or "",
        )

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if session_id:
        pid_str = os.environ.get("CLAUDE_PID")
        try:
            pid = int(pid_str) if pid_str else 0
        except ValueError:
            pid = 0
        return claims.Owner(
            session_id=session_id,
            pid=pid,
            proc_start="",
            name="",
            agent_id=os.environ.get("CLAUDE_AGENT_ID") or None,
        )

    return None


# ---------------------------------------------------------------------------
# Formatacao humana
# ---------------------------------------------------------------------------


def _humanize_age(ms_ref: int | None, agora_ms: int | None = None) -> str:
    if ms_ref is None:
        return "sem registro"
    agora_ms = agora_ms if agora_ms is not None else int(time.time() * 1000)
    try:
        delta_s = max(0, (agora_ms - int(ms_ref)) // 1000)
    except (TypeError, ValueError):
        return "sem registro"
    if delta_s < 60:
        return f"há {delta_s}s"
    delta_min = delta_s // 60
    if delta_min < 60:
        return f"há {delta_min}min"
    delta_h = delta_min // 60
    resto_min = delta_min % 60
    if delta_h < 24:
        return f"há {delta_h}h{resto_min:02d}min" if resto_min else f"há {delta_h}h"
    delta_d = delta_h // 24
    return f"há {delta_d}d"


def _fmt_range(faixa) -> str:
    if not faixa:
        return "arquivo inteiro"
    return f"{faixa[0]}-{faixa[1]}"


def _fmt_ranges(claim) -> str:
    """Todas as faixas do turno (T-026). Ler so `claim.range` mostraria a
    ultima edicao como se fosse a unica -- o `status`/`who` passaria a
    discordar do aviso que a peer recebe, que olha o conjunto."""
    faixas = list(getattr(claim, "ranges", None) or [])
    if not faixas:
        return _fmt_range(getattr(claim, "range", None))
    return ", ".join(f"{a}-{b}" for a, b in faixas)


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    larguras = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            larguras[i] = max(larguras[i], len(cell))
    linha_fmt = "  ".join("{:<" + str(w) + "}" for w in larguras)
    print(linha_fmt.format(*headers))
    print(linha_fmt.format(*["-" * w for w in larguras]))
    for row in rows:
        print(linha_fmt.format(*row))


def _session_to_dict(s: sessions.Session, agora_ms: int) -> dict:
    return {
        "nome": s.name,
        "pid": s.pid,
        "cwd": s.cwd,
        "status_rotulo": s.status,
        "updated_at": s.updated_at,
        "atualizado": _humanize_age(s.updated_at, agora_ms),
        "session_id": s.session_id,
    }


def _claim_to_dict(c: claims.Claim, agora_ms: int, ativo: bool) -> dict:
    return {
        "resource": c.resource,
        "path": c.path,
        "range": list(c.range) if c.range else None,
        "dono": c.owner.name or c.owner.session_id,
        "session_id": c.owner.session_id,
        "pid": c.owner.pid,
        "acquired_at": c.acquired_at,
        "ocupado": _humanize_age(c.acquired_at, agora_ms),
        "proposito": c.purpose,
        "scope": c.scope,
        "ativo": ativo,
    }


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    agora_ms = int(time.time() * 1000)

    dir_sessoes_existe = sessions.sessions_dir().exists()
    vivas = sessions.peers(exclude_pid=-1)
    indice = {s.session_id: s for s in vivas if s.session_id}

    dir_claims_existe = _claims_dir_exists()
    todos_claims = _all_claims() if dir_claims_existe else []

    # Achado ALTA (11/09): resolve os donos DESCONHECIDOS (fora do indice de
    # peers vivas) em paralelo ANTES da classificacao abaixo, para nao pagar
    # um OpenProcess sequencial por claim -- ver _prefetch_esta_vivo_padrao.
    # So entram claims NAO expiradas (expirada ja cai em pendentes_sweep sem
    # precisar de esta_vivo, curto-circuito que ja existia).
    donos_a_checar = [
        c.owner
        for c in todos_claims
        if c.owner.session_id not in indice and not _is_expired(c, agora_ms)
    ]
    cache_liveness = _prefetch_esta_vivo_padrao(donos_a_checar)
    esta_vivo = _make_esta_vivo(indice, cache=cache_liveness)

    ocupados: list[claims.Claim] = []
    pendentes_sweep: list[claims.Claim] = []
    for c in todos_claims:
        if _is_expired(c, agora_ms) or not esta_vivo(c.owner):
            pendentes_sweep.append(c)
        else:
            ocupados.append(c)

    if args.json:
        payload = {
            "sessoes_dir_existe": dir_sessoes_existe,
            "sessoes": [_session_to_dict(s, agora_ms) for s in vivas],
            "claims_dir_existe": dir_claims_existe,
            "recursos_ocupados": [_claim_to_dict(c, agora_ms, True) for c in ocupados],
            "recursos_pendentes_sweep": [
                _claim_to_dict(c, agora_ms, False) for c in pendentes_sweep
            ],
        }
        _print_json(payload, indent=2)
        return 0

    print("SESSOES VIVAS")
    if not dir_sessoes_existe:
        print("  (diretório de sessões do harness não encontrado — nada para listar)")
    elif not vivas:
        print("  Nenhuma sessão viva no momento.")
    else:
        cabecalho = ["Nome", "PID", "CWD", "Status (rótulo)", "Atualizado"]
        linhas = [
            [
                s.name or "?",
                str(s.pid) if s.pid is not None else "?",
                s.cwd or "?",
                s.status or "?",
                _humanize_age(s.updated_at, agora_ms),
            ]
            for s in vivas
        ]
        _print_table(cabecalho, linhas)
        print("  (\"Status\" é o rótulo que a própria sessão reportou — não é tempo real;")
        print("   julgue pela coluna \"Atualizado\".)")

    print()
    print("RECURSOS OCUPADOS")
    if not dir_claims_existe:
        print(
            "  Estado de coordenação ainda não inicializado "
            "(nenhuma claim foi criada nesta máquina)."
        )
    elif not ocupados:
        print("  Nenhum recurso ocupado no momento.")
    else:
        cabecalho = ["Caminho", "Faixa", "Dono", "Ocupado desde", "Propósito"]
        linhas = [
            [
                c.path,
                _fmt_ranges(c),
                c.owner.name or c.owner.session_id,
                _humanize_age(c.acquired_at, agora_ms),
                c.purpose or "(sem descrição)",
            ]
            for c in ocupados
        ]
        _print_table(cabecalho, linhas)

    if pendentes_sweep:
        print()
        plural = "claim" if len(pendentes_sweep) == 1 else "claims"
        print(
            f"  {len(pendentes_sweep)} {plural} de dono morto/expirado ainda em disco "
            "— rode `ccoord sweep`."
        )

    return 0


def cmd_who(args: argparse.Namespace) -> int:
    agora_ms = int(time.time() * 1000)
    alvo = args.recurso

    indice = _session_index()
    esta_vivo = _make_esta_vivo(indice)

    achados: list[claims.Claim] = list(claims.overlapping(alvo, None, esta_vivo=esta_vivo))
    chaves = {c.resource for c in achados}
    exato = claims.owner_of(alvo, esta_vivo=esta_vivo)
    if exato is not None and exato.resource not in chaves:
        achados.append(exato)

    if args.json:
        payload = {"recurso": alvo, "claims": [_claim_to_dict(c, agora_ms, True) for c in achados]}
        _print_json(payload, indent=2)
        return 0

    if not achados:
        print(f"{alvo}: livre (nenhum dono vivo).")
        return 0

    for c in achados:
        dono = c.owner.name or c.owner.session_id
        print(
            f"{c.path} [{_fmt_ranges(c)}] — dono: {dono} "
            f"(ocupado {_humanize_age(c.acquired_at, agora_ms)}) — "
            f"{c.purpose or '(sem descrição)'}"
        )
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    if not args.mine:
        print("Uso: ccoord release --mine")
        return 2

    dono = _current_owner()
    if dono is None:
        msg = (
            "Não foi possível identificar esta sessão "
            "(defina CLAUDE_CODE_SESSION_ID ou rode dentro do Claude Code)."
        )
        if args.json:
            _print_json({"ok": False, "erro": msg})
        else:
            print(msg)
        return 1

    removidos = claims.release(dono, scope="session")

    if args.json:
        _print_json({"ok": True, "session_id": dono.session_id, "removidos": removidos})
        return 0

    nome = dono.name or dono.session_id
    if removidos == 0:
        print(f"Nenhuma claim para liberar (sessão {nome} não detém nada).")
    else:
        plural = "claim" if removidos == 1 else "claims"
        print(f"Liberada(s) {removidos} {plural} da sessão {nome}.")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    indice = _session_index()
    esta_vivo = _make_esta_vivo(indice)
    removidos = claims.sweep(esta_vivo=esta_vivo)

    if args.json:
        _print_json({"removidos": removidos})
        return 0

    if removidos == 0:
        print("Nenhuma claim de dono morto encontrada.")
    else:
        plural = "claim" if removidos == 1 else "claims"
        print(f"Removida(s) {removidos} {plural} de dono morto.")
    return 0


# ---------------------------------------------------------------------------
# argparse / entrypoint
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccoord",
        description="Visibilidade sob demanda da coordenação entre sessões (RF-08).",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_status = sub.add_parser(
        "status", help="mapa das sessões vivas e dos recursos ocupados"
    )
    p_status.add_argument("--json", action="store_true", help="saída em JSON")
    p_status.set_defaults(func=cmd_status)

    p_who = sub.add_parser("who", help="quem detém um recurso, ou se está livre")
    p_who.add_argument("recurso", help="caminho de arquivo ou chave de recurso")
    p_who.add_argument("--json", action="store_true", help="saída em JSON")
    p_who.set_defaults(func=cmd_who)

    p_release = sub.add_parser("release", help="libera claims")
    p_release.add_argument(
        "--mine", action="store_true", help="libera as claims desta sessão"
    )
    p_release.add_argument("--json", action="store_true", help="saída em JSON")
    p_release.set_defaults(func=cmd_release)

    p_sweep = sub.add_parser("sweep", help="remove claims de dono morto")
    p_sweep.add_argument("--json", action="store_true", help="saída em JSON")
    p_sweep.set_defaults(func=cmd_sweep)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # regra 2: nunca traceback, mesmo num caminho nao previsto
        print(f"Erro inesperado em `ccoord {args.comando}`: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
