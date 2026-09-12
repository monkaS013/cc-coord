#!/usr/bin/env python
"""coord_pre_write.py - PreToolUse para Edit/Write/NotebookEdit.

Entrypoint FINO (T-09): so le stdin, chama `ccoord`, emite. A logica mora em
`ccoord.classify`/`ccoord.claims`/`ccoord.policy`/`ccoord.hookio` (nao
alterados aqui). Ler antes de mexer:
  - .specs/coordenacao-multissessao/medicao-hooks.md (payloads medidos)
  - .specs/coordenacao-multissessao/design.md secoes 3.5, 3.7, 3.8
  - .specs/coordenacao-multissessao/tasks.md T-09

O que este arquivo faz, na ordem:
  1. Classifica o `tool_input` em recursos (`classify.classify`) — so
     resultados de kind="file" (Edit/Write/NotebookEdit nunca produzem outro
     kind).
  2. Para cada recurso, TENTA adquirir o claim (`claims.claim`, escopo
     "turn", TTL 15 min): se conseguir (livre ou renovacao do meu proprio
     claim), nao ha conflito. Se `held_by_peer`, o claim devolvido vira o
     `owner` que `policy.decide` usa para montar o aviso prescritivo.
  3. Registra um carimbo de "escrita propria" (`<CCOORD_HOME>/own_writes/`)
     para este path — e o que permite `coord_file_changed.py` filtrar o eco
     da propria sessao (AC-015): o `FileChanged` que o harness dispara para
     esta MESMA escrita, ~0.6s depois, compara com este carimbo.
  4. Consome (le e apaga) um carimbo de alteracao PENDENTE de peer para este
     mesmo path exato, se houver (AC-016) — dado que "a alteracao pode ter
     entrado por fora" desde a ultima vez que o modelo tocou neste arquivo.
  5. Combina as decisoes (pior nivel vence: deny > warn > allow) e devolve
     para `hookio.executar`, que decide o formato de saida. "Combina" e
     literal (achado 2 da auditoria 11/09): TODOS os avisos do pior nivel
     encontrado sao concatenados na razao final, nao so o primeiro — um
     conflito de peer (warn) e um carimbo de mudanca externa pendente
     (tambem warn) no mesmo recurso agora aparecem OS DOIS, nunca so um.

Regra 1 (critica): o PROCESSO deste hook sempre sai com exit 0. O que
`hookio.executar` devolve (0 ou 1) e um sinal logico interno, nunca vira
`sys.exit`. Quem bloqueia de verdade e o JSON `permissionDecision:"deny"`
dentro do invólucro, lido pelo harness so no caminho de sucesso (Exit code
diferente de 0 em PreToolUse cai em "show stderr to user only but continue
with tool call" — o oposto do que se quer). Ver medicao-hooks.md secao 3.

Regra 8 (import de `ccoord`): depois de instalado (T-12, ainda nao feita),
este arquivo roda de `~/.claude/hooks/coord_pre_write.py`, fora do repo —
nao ha `src/` irmao la. `_bootstrap_src_path()` resolve isso: primeiro tenta
`CCOORD_SRC` (env, e o que a instalacao vai configurar), com fallback para
`<repo>/src` relativo a ESTE arquivo (funciona hoje, rodando do repo).

Regra 7: nenhuma excecao escapa. `hookio.executar` ja protege TUDO que
acontece dentro do `decisor` (classify/claims/policy incluidos) — captura,
registra em `events.log` e degrada para silencio. O `try/except` em `main()`
so cobre o que fica FORA disso: a resolucao de path e o `import` do proprio
`ccoord` (se falharem, nao ha como sequer chamar `hookio`, entao o fallback
aqui e so imprimir `{}` — nao ha como logar, o modulo que loga e o que
falhou em importar).
"""

from __future__ import annotations

import json
import os
import sys
import time


def _bootstrap_src_path() -> None:
    src = os.environ.get("CCOORD_SRC")
    if not src or not os.path.isdir(os.path.join(src, "ccoord")):
        # Achado 5 (auditoria adversarial 11/09): CCOORD_SRC setado mas
        # apontando para um diretorio que nao existe mais (repo
        # renomeado/movido, disco USB/OneDrive reorganizado) era usado do
        # mesmo jeito -- so o caso VAZIO caia no fallback. `from ccoord
        # import ...` falhava com ModuleNotFoundError ANTES de hookio.py
        # existir para logar (o proprio modulo que logaria e o que falhou em
        # importar); cada `main()` degrada essa excecao para `{}` (allow em
        # tudo) -- bypassando TODA a politica, inclusive o fail-closed do
        # kill (AC-010) e o deny de git commit/push com peer viva (AC-007),
        # sem NENHUM rastro (nem events.log chega a existir). Agora tambem
        # cai no fallback quando o env aponta para algo que nao e um
        # diretorio, ou e um diretorio sem o PACOTE `ccoord/` dentro (nao so
        # "esta vazio") -- checar so `isdir(src)` aceitaria uma pasta
        # existente mas errada e continuaria quebrando o import do mesmo
        # jeito.
        aqui = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(os.path.dirname(aqui), "src")
    if src not in sys.path:
        sys.path.insert(0, src)


# ---------------------------------------------------------------------------
# Estado em disco fora de claims/ (carimbos de escrita propria e de mudanca
# pendente) — schema documentado aqui e em coord_file_changed.py /
# coord_post_batch.py (que precisam ler o MESMO formato). Duplicado por
# restricao da task (so estes 7 arquivos + teste podem ser criados/alterados
# — nao ha modulo comum para hospedar isto).
# ---------------------------------------------------------------------------


def _ccoord_home() -> str:
    return os.environ.get("CCOORD_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "coord"
    )


def _slug_path(path: str) -> str:
    """Slug deterministico de um caminho, para nome de arquivo em disco.

    Tem que produzir EXATAMENTE o mesmo resultado em coord_file_changed.py
    (quem escreve o carimbo de mudanca externa e le/escreve o de eco) e em
    coord_post_batch.py (quem tambem consome carimbos de mudanca) — daí a
    normalizacao simples e sem dependencia de nada alem de string.
    
    FONTE UNICA desde 12/09: delega para `ccoord.carimbos.slug_path`. A copia
    local aqui divergiu quando a resolucao de nome curto 8.3 entrou no modulo
    comum -- escritor e leitor do carimbo passaram a usar chaves diferentes, e
    o sintoma (aviso falso de mudanca externa) aponta para o lugar errado.
    """
    try:
        from ccoord.carimbos import slug_path

        return slug_path(path)
    except Exception:  # noqa: BLE001 - fail-open: normalizacao antiga, nunca quebrar o turno
        normalizado = (path or "").strip().replace("\\", "/").lower()
        return "".join(c if (c.isalnum() or c in "-._") else "_" for c in normalizado) or "arquivo"


def _marcar_escrita_propria(path: str, session_id: str) -> None:
    """Carimba 'este path foi escrito por esta sessao agora' (best-effort).

    `coord_file_changed.py` compara (path, session_id, janela de tempo) com
    isto para decidir se o `FileChanged` que ele recebeu e o eco da propria
    escrita (AC-015) ou uma mudanca de fato externa (AC-016). Nunca levanta:
    e um efeito colateral de conveniencia, nao uma trava.
    """
    try:
        home = _ccoord_home()
        own_dir = os.path.join(home, "own_writes")
        os.makedirs(own_dir, exist_ok=True)
        caminho = os.path.join(own_dir, _slug_path(path) + ".json")
        # `consumido: False` explicito (achado 6, ver coord_file_changed.py):
        # toda escrita NOVA da mesma sessao neste mesmo path reseta o
        # carimbo do zero, dando um credito de eco fresco para o proximo
        # FileChanged -- mesmo que o carimbo anterior ja tivesse sido
        # consumido por um FileChanged de uma edicao anterior.
        dados = {
            "path": path,
            "session_id": session_id,
            "ts": int(time.time() * 1000),
            "consumido": False,
        }
        with open(caminho, "w", encoding="utf-8") as fh:
            json.dump(dados, fh, ensure_ascii=False)
    except OSError:
        pass


def _consumir_carimbo_pendente(path: str) -> dict | None:
    """Le e APAGA o carimbo de mudanca pendente para este path exato, se houver.

    Consumir (apagar) e o que evita repetir o mesmo aviso em toda chamada
    seguinte — o AC-016 pede "aparece no PROXIMO PreToolUse/PostToolBatch",
    nao "aparece para sempre".
    """
    try:
        home = _ccoord_home()
        caminho = os.path.join(home, "changed", _slug_path(path) + ".json")
        with open(caminho, "r", encoding="utf-8") as fh:
            dados = json.load(fh)
    except Exception:
        return None
    try:
        os.remove(caminho)
    except OSError:
        pass
    return dados


def _fmt_carimbo(dados: dict) -> str:
    caminho = dados.get("path", "?")
    evento = dados.get("event", "?")
    ts = dados.get("ts")
    if isinstance(ts, (int, float)):
        horario = time.strftime("%H:%M:%S", time.localtime(ts / 1000))
    else:
        horario = "horario desconhecido"
    return (
        f"AVISO: {caminho} mudou em disco ({evento}) as {horario}, por um processo "
        "que nao foi esta sessao (o sensor FileChanged nao identifica quem — "
        "so o registro de claims disse 'nao fui eu'). Confira o conteudo antes "
        "de continuar editando; se for outra sessao, considere `ccoord who` "
        "e `SendMessage` antes de prosseguir."
    )


# ---------------------------------------------------------------------------
# decisor
# ---------------------------------------------------------------------------


def _ler_arquivo(path: str):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _construir_decisor(hookio, sessions, claims, classify, policy):
    from types import SimpleNamespace

    def _decisor(payload: dict):
        tool_name = payload.get("tool_name")
        if tool_name not in ("Edit", "Write", "NotebookEdit"):
            return None

        tool_input = payload.get("tool_input") or {}
        cwd = payload.get("cwd") or ""

        recursos = classify.classify(tool_name, tool_input, cwd, ler_arquivo=_ler_arquivo)
        if not recursos:
            return None

        dono = hookio.identidade(payload)
        me = sessions.me(payload.get("session_id"))
        exclude_pid = me.pid if me is not None else None

        # Caminho quente (RNF-04/T-017): `sessions.peers()` varre TODO
        # arquivo de sessao em disco e checa liveness real (OpenProcess +
        # GetProcessTimes por peer, via ctypes) - o eixo mais caro medido em
        # medicao-perf.md secao 5. Para "file"/Edit-Write, `policy.decide()`
        # so CONSULTA `peers` quando ha um `owner_claim` de verdade (peer ja
        # detem este recurso exato) - `_claim_de_peer_viva()` em policy.py
        # devolve False de cara quando `owner is None`, sem tocar a lista.
        # Isso deixa `peers_vivas=[]` EQUIVALENTE ao valor real sempre que
        # nenhum recurso deste lote tiver dono - ou seja, no caso comum (a
        # imensa maioria das chamadas de Edit/Write nao colide com nada),
        # calculamos os proprios claims (leitura/gravacao barata, um
        # `os.open` por recurso) e so pagamos o scan de sessoes vivas quando
        # ESTE hook ja sabe, pelo proprio `claims.claim()`, que existe uma
        # peer para checar. `claims.claim()`/`_marcar_escrita_propria()`/
        # `_consumir_carimbo_pendente()` continuam rodando SEMPRE, sem
        # excecao - nenhum efeito colateral e pulado, so o calculo de
        # `peers_vivas` e adiado.
        peers_cache: list = []
        peers_calculadas = False

        def _peers_vivas():
            nonlocal peers_cache, peers_calculadas
            if not peers_calculadas:
                peers_cache = sessions.peers(exclude_pid=exclude_pid)
                peers_calculadas = True
            return peers_cache

        decisoes = []
        for recurso in recursos:
            if recurso.path:
                _marcar_escrita_propria(recurso.path, dono.session_id)

            owner_claim = None
            resultado = claims.claim(
                recurso.id,
                dono,
                ttl_s=900,
                meta={
                    "path": recurso.path or recurso.id,
                    "range": list(recurso.lines) if recurso.lines else None,
                    "scope": "turn",
                    "purpose": f"{recurso.action} via PreToolUse ({tool_name})",
                },
            )
            if not resultado.ok:
                owner_claim = resultado.claim

            peers_para_decisao = _peers_vivas() if owner_claim is not None else []
            decisoes.append(policy.decide(recurso, owner_claim, me, peers_para_decisao, contexto={}))

            if recurso.path:
                carimbo = _consumir_carimbo_pendente(recurso.path)
                if carimbo is not None:
                    decisoes.append(
                        SimpleNamespace(verdict="warn", reason=_fmt_carimbo(carimbo))
                    )

        # Achado 2 (auditoria 11/09): antes, este laco devolvia so o
        # PRIMEIRO item de `decisoes` do pior verdict -- mas
        # `_consumir_carimbo_pendente()` (acima) ja tinha APAGADO do disco o
        # carimbo de mudanca externa antes mesmo de saber se ele ia caber na
        # resposta. Quando um conflito de peer (warn, de `policy.decide`) e
        # um carimbo pendente (tambem warn) coincidiam no MESMO recurso, so o
        # primeiro chegava ao modelo -- o outro tinha sido consumido do disco
        # e ficava perdido para sempre (nao ha como reconsulta-lo depois).
        # Combina TODOS os avisos do pior nivel encontrado (deny > warn) numa
        # unica razao: nada que ja foi lido/apagado do disco pode deixar de
        # aparecer na resposta. Os textos individuais (policy.py: 2000
        # chars/20 linhas; carimbo: poucas linhas) sao pequenos -- a soma
        # tipica fica bem abaixo do teto de 8000 chars/200 linhas que
        # `hookio.emitir_contexto` aplica de qualquer forma como rede final.
        denies = [d for d in decisoes if d.verdict == "deny"]
        if denies:
            return SimpleNamespace(verdict="deny", reason="\n\n".join(d.reason for d in denies))
        warns = [d for d in decisoes if d.verdict == "warn"]
        if warns:
            return SimpleNamespace(verdict="warn", reason="\n\n".join(d.reason for d in warns))
        return None

    return _decisor


def main() -> int:
    try:
        _bootstrap_src_path()
        from ccoord import hookio, sessions, claims, classify, policy

        payload = hookio.ler_payload()
        decisor = _construir_decisor(hookio, sessions, claims, classify, policy)
        hookio.executar(payload, decisor)
    except Exception:
        try:
            sys.stdout.write("{}\n")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
