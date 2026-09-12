#!/usr/bin/env python
"""coord_pre_bash.py - PreToolUse para Bash.

Entrypoint FINO (T-09) — mesma postura de coord_pre_write.py, ver o docstring
la para o essencial (regra 1: exit 0 sempre; regra 7: excecoes internas nunca
escapam, cobertas por `hookio.executar`; regra 8: import de `ccoord` via
`CCOORD_SRC` com fallback relativo a este arquivo).

O que e especifico deste arquivo:
  - So classifica quando `tool_name == "Bash"` (Edit/Write vao por
    `coord_pre_write.py`; separacao por `matcher` no `settings.json` e T-12,
    mas o filtro aqui e defensivo caso os dois acabem recebendo o mesmo
    evento).
  - `classify.classify("Bash", ...)` pode devolver VARIOS recursos numa
    chamada so: kill, git write, bind de porta/servidor, migracao — a pior
    decisao entre eles vence (deny > warn > allow).
  - Recursos `port`/`server` (bind): TENTA adquirir o claim (escopo
    "session", TTL 60 min — design.md 3.2) do mesmo jeito que
    coord_pre_write.py faz com arquivo; um conflito vira o `owner` para
    `policy.decide`.
  - Recursos `process`/`browser` (kill) e `git`/`db`: NAO adquire claim —
    kill e git commit/push sao acoes momentaneas, nao "posse" de um recurso;
    so consulta `claims.owner_of()` (so importa de fato para o ramo de kill;
    git/db calculam peer no mesmo repo direto de `peers`, ignorando `owner`).
  - **Divida herdada da T-006** (tasks.md, "Dívida herdada"): `policy.py` e
    pura e nao pode abrir socket, entao ela so tem `porta+1` como palpite
    quando `contexto["porta_livre"]` nao vem preenchido — e um palpite pode
    estar ocupado, o que faria o aviso sugerir uma porta que TAMBEM esta
    tomada (mentira pratica). Este arquivo mede de verdade: varre portas a
    partir de `ocupada+1` fazendo um `bind()` real em `127.0.0.1` ate achar
    uma livre, e so entao preenche `contexto["porta_livre"]`.
  - **T-016 (AC-007): `contexto["commits_alheios"]` de verdade.** `policy.py`
    ja consumia essa chave desde a T-006, mas nenhum entrypoint a preenchia
    — o deny de `git commit`/`push` citava a peer viva, nunca o commit. Este
    arquivo agora roda, SO no ramo de git write (`commit`/`push`), duas
    consultas reais ao git: descobre o upstream (`rev-parse --abbrev-ref
    --symbolic-full-name @{u}`) e, se existir, lista os commits locais que
    ainda nao chegaram la (`log <upstream>..HEAD --oneline`). Regras que nao
    podem ser erradas (ver `_commits_alheios` abaixo): `--no-optional-locks`
    em toda chamada (contencao de `index.lock` com as peers); NUNCA assumir
    `main`/`master` — repo sem upstream devolve lista vazia, que e resposta
    legitima (a politica ja recusa pela peer viva, so sem citar commit);
    timeout <=1,5s por chamada, git ausente/lento nunca propaga (`except`
    generico -> lista vazia); e o custo so e pago no ramo de git write —
    Edit/Write (que nem passam por este arquivo) e Bash comum (git ausente
    do comando, ou git de leitura) nunca chamam `_commits_alheios`.
"""

from __future__ import annotations

import os
import socket
import sys


def _bootstrap_src_path() -> None:
    src = os.environ.get("CCOORD_SRC")
    if not src or not os.path.isdir(os.path.join(src, "ccoord")):
        # Achado 5 (auditoria 11/09): CCOORD_SRC setado mas apontando para um
        # diretorio que nao existe mais (repo renomeado/movido/disco
        # reorganizado) derrubava `from ccoord import ...` ANTES de hookio.py
        # existir para logar o erro -- os 7 hooks viravam no-op silencioso
        # (allow em tudo, inclusive kill), sem nenhum rastro em lugar nenhum.
        # Cai no fallback relativo tambem quando o env aponta para lixo (nao
        # so quando esta vazio) -- checa o PACOTE (`ccoord/` dentro do dir),
        # nao so que o diretorio exista, para nao aceitar um CCOORD_SRC que
        # aponta para uma pasta valida mas errada.
        aqui = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(os.path.dirname(aqui), "src")
    if src not in sys.path:
        sys.path.insert(0, src)


# ---------------------------------------------------------------------------
# Porta livre DE VERDADE (regra 5 do prompt da task / divida da T-006)
# ---------------------------------------------------------------------------

_PORTA_MAX = 65535
_TENTATIVAS_PORTA_LIVRE = 30


def _porta_livre_a_partir_de(porta_ocupada: int, tentativas: int = _TENTATIVAS_PORTA_LIVRE):
    """Acha, com `bind()` real em 127.0.0.1, uma porta livre a partir de
    `porta_ocupada + 1`. Devolve None se nada livre for encontrado dentro do
    numero de tentativas (o chamador degrada para mensagem generica).

    `policy.py` e pura de proposito (T-06) — nao pode fazer I/O de socket.
    Esta funcao e o "quem chama ja mediu" que o design.md secao 3.4 e o
    docstring de `policy.decide` pedem para o campo `contexto["porta_livre"]`.
    """
    candidato = porta_ocupada + 1
    for _ in range(tentativas):
        if candidato > _PORTA_MAX:
            return None
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", candidato))
            return candidato
        except OSError:
            candidato += 1
        finally:
            s.close()
    return None


def _numero_porta(resource_id: str):
    try:
        return int(resource_id.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# T-016 (AC-007): commits alheios de verdade, so no ramo de git write.
# ---------------------------------------------------------------------------

_ACOES_GIT_ESCRITA = ("commit", "push")
_GIT_TIMEOUT_S = 1.5
_GIT_COMMITS_LIMITE = 5


def _rodar_git(args, cwd: str):
    """Roda `git --no-optional-locks <args>` em `cwd`, com timeout curto.

    Nunca propaga: git ausente (`FileNotFoundError`), timeout
    (`subprocess.TimeoutExpired`), erro de encoding, qualquer coisa — vira
    `None`, que o chamador trata como "sem resposta util" (lista vazia).
    `--no-optional-locks` (regra 1) evita contencao de `index.lock` com as
    outras sessoes que possam estar rodando git no mesmo repo agora.

    Import de `subprocess` e local de proposito (nao no topo do arquivo):
    este ramo so roda para `git commit`/`push` (raro), entao o custo do
    import nao pode ser pago por TODA chamada de `coord_pre_bash.py` —
    inclusive Bash comum sem nenhum git (regra 4/RNF-04).
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks"] + list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _commits_alheios(repo_path: str):
    """Commits locais (HEAD) que ainda nao chegaram ao upstream, formatados
    como `log --oneline` (hash curto + assunto), limitados a
    `_GIT_COMMITS_LIMITE` linhas + "e outros N" se passar disso.

    Regra 2: descobre o upstream de verdade (`@{u}`) em vez de assumir
    `main` — um repo em `master` (ex.: dashboard-inteligencia-mercado) ou
    sem upstream nenhum nao pode virar comparacao inventada. Sem upstream,
    a resposta legitima e lista vazia (a politica ja recusa pela peer viva
    no mesmo repo; so fica sem citar commit, que e o unico jeito honesto).
    """
    if not repo_path:
        return []

    upstream_out = _rodar_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], repo_path
    )
    if not upstream_out:
        return []
    upstream = upstream_out.strip()
    if not upstream:
        return []

    log_out = _rodar_git(["log", f"{upstream}..HEAD", "--oneline"], repo_path)
    if not log_out:
        return []

    linhas = [l.strip() for l in log_out.splitlines() if l.strip()]
    if not linhas:
        return []
    if len(linhas) > _GIT_COMMITS_LIMITE:
        resto = len(linhas) - _GIT_COMMITS_LIMITE
        linhas = linhas[:_GIT_COMMITS_LIMITE] + [f"e outros {resto}"]
    return linhas


# ---------------------------------------------------------------------------
# decisor
# ---------------------------------------------------------------------------


def _construir_decisor(hookio, sessions, claims, classify, policy, carimbos):
    def _decisor(payload: dict):
        tool_name = payload.get("tool_name")
        if tool_name != "Bash":
            return None

        tool_input = payload.get("tool_input") or {}
        cwd = payload.get("cwd") or ""

        recursos = classify.classify("Bash", tool_input, cwd)
        if not recursos:
            return None

        dono = hookio.identidade(payload)
        me = sessions.me(payload.get("session_id"))
        exclude_pid = me.pid if me is not None else None

        # Caminho quente (RNF-04/T-017): so paga o scan de `sessions.peers()`
        # (o eixo mais caro medido - liveness real via ctypes por sessao em
        # disco) quando `policy.decide()` de fato vai CONSULTAR a lista.
        # Mirroring das regras de `ccoord.policy.decide()` (mesmas condicoes
        # de despacho por `kind`/`action`, nao uma logica nova):
        #   - git (qualquer acao) e db/migrate: SEMPRE consultam peers,
        #     mesmo sem claim nenhuma (nao ha claim para git/db aqui).
        #   - port/server bind: so consulta peers se HOUVER owner_claim
        #     (`_claim_de_peer_viva` devolve False de cara com owner=None).
        #   - kill (process/browser): NUNCA consulta peers (regra 2 do
        #     design - decide so por owner/me).
        # Passo 1 roda claim()/owner_of() (baratos: no maximo um os.open ou
        # uma leitura de arquivo por recurso) sem tocar em peers.
        itens = []  # (recurso, owner_claim, contexto)
        precisa_peers = False
        for recurso in recursos:
            owner_claim = None
            contexto = {}

            if recurso.kind == "file":
                # Escrita de arquivo VIA BASH (redirecionamento, `sed -i`,
                # `Set-Content`, `tee`, `python -c`): o classify passou a
                # reconhecer essas formas, mas este hook nao tinha ramo para
                # `file` -- caiam no `else` (kill), que por desenho NUNCA
                # consulta peers. Resultado medido pelo conferente da 3a
                # auditoria: escrever por Bash num arquivo com claim de peer
                # viva devolvia `{}` (allow silencioso), enquanto o mesmo
                # arquivo por `Edit` avisava. O bypass anulava a correcao do
                # classify: o gate reconhecia o recurso e nao usava.
                # Espelha o que `coord_pre_write.py` faz: tenta o claim, e se
                # ja houver dono, e ele que vai para a politica -- que so
                # decide se a peer aparecer em `peers` (dai o precisa_peers).
                resultado = claims.claim(
                    recurso.id,
                    dono,
                    ttl_s=900,
                    meta={
                        "path": recurso.path or recurso.id,
                        "range": list(recurso.lines) if recurso.lines else None,
                        "scope": "turn",
                        "purpose": f"escrita via Bash ({recurso.action}) em {recurso.path or recurso.id}",
                    },
                )
                if not resultado.ok:
                    owner_claim = resultado.claim
                if owner_claim is not None:
                    precisa_peers = True

                # Carimbar a escrita propria e OBRIGATORIO aqui, pelo mesmo
                # motivo que em coord_pre_write.py: sem isto, o `FileChanged`
                # disparado pela nossa propria escrita via Bash nao e
                # reconhecido como eco (AC-015) e a sessao recebe, no proximo
                # Edit, um aviso FALSO de "mudou em disco por outro processo".
                # Esquecer esta linha foi achado ALTA da 4a auditoria (12/09) --
                # a correcao do ramo `file` estava pela metade.
                if recurso.path:
                    carimbos.marcar_escrita_propria(recurso.path, dono.session_id)
                    pendente = carimbos.consumir_carimbo_pendente(recurso.path)
                    if pendente:
                        contexto["mudanca_externa"] = pendente
            elif recurso.kind in ("port", "server") and recurso.action == "bind":
                numero = _numero_porta(recurso.id)
                if numero is not None:
                    contexto["porta_livre"] = _porta_livre_a_partir_de(numero)
                resultado = claims.claim(
                    recurso.id,
                    dono,
                    ttl_s=3600,
                    meta={
                        "path": recurso.path or recurso.id,
                        "scope": "session",
                        "purpose": f"bind {recurso.id} via PreToolUse (Bash)",
                    },
                )
                if not resultado.ok:
                    owner_claim = resultado.claim
                if owner_claim is not None:
                    precisa_peers = True
            elif recurso.kind in ("git", "db"):
                # policy.decide() ignora `owner` para git/db (decide so por
                # peers no mesmo repo) - claims.owner_of() aqui seria leitura
                # descartada, entao nem chamamos.
                precisa_peers = True
                if recurso.kind == "git" and recurso.action in _ACOES_GIT_ESCRITA:
                    # T-016: so o ramo de git write paga o custo de consultar
                    # git de verdade (regra 4) - `reset`/`checkout` (warn, nao
                    # deny) e `db` seguem sem essa chamada.
                    contexto["commits_alheios"] = _commits_alheios(
                        recurso.path or recurso.id
                    )
            else:
                # kill: acao momentanea, nunca adquire claim, nunca usa peers.
                # AC-010 (fail-closed): `owner_of()` degrada em silencio, entao
                # estado corrompido devolveria None -- indistinguivel de "recurso
                # livre" -- e o kill sairia LIBERADO. Achado por auditoria
                # adversarial em 11/09 reproduzindo com CCOORD_HOME apontando
                # para um arquivo: a saida era `{}` (allow). Aqui a flag e
                # calculada de verdade; a politica ja sabe recusar com ela.
                # Duas ilegibilidades DIFERENTES, ambas fail-closed para kill:
                #   1. o diretorio de estado inteiro (CCOORD_HOME corrompido)
                #   2. o arquivo de claim DESTE recurso (JSON truncado) -- achado
                #      pela 2a auditoria: `owner_of()` devolveria None e o kill
                #      sairia liberado mesmo com dono vivo.
                if not claims.estado_legivel() or claims.claim_ilegivel(recurso.id):
                    contexto["estado_ilegivel"] = True
                    owner_claim = None
                else:
                    owner_claim = claims.owner_of(recurso.id)

            itens.append((recurso, owner_claim, contexto))

        peers_vivas = sessions.peers(exclude_pid=exclude_pid) if precisa_peers else []

        decisoes = [
            policy.decide(recurso, owner_claim, me, peers_vivas, contexto=contexto)
            for (recurso, owner_claim, contexto) in itens
        ]

        for d in decisoes:
            if d.verdict == "deny":
                return d
        for d in decisoes:
            if d.verdict == "warn":
                return d
        return None

    return _decisor


def main() -> int:
    try:
        _bootstrap_src_path()
        from ccoord import hookio, sessions, claims, classify, policy, carimbos

        payload = hookio.ler_payload()
        decisor = _construir_decisor(hookio, sessions, claims, classify, policy, carimbos)
        hookio.executar(payload, decisor)
    except Exception:
        try:
            sys.stdout.write("{}\n")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
