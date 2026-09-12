#!/usr/bin/env python
"""coord_session_end.py - SessionEnd: libera TODAS as claims da sessao.

Entrypoint FINO (T-09). Ver coord_pre_write.py para as regras 1/7/8 comuns.

Regra medida (medicao-hooks.md secao 3, AC-014): `SessionEnd` REJEITA o
invólucro `hookSpecificOutput` na validacao do harness — emitir ali (deny ou
contexto) gera erro visivel ao usuario e nada mais. Este hook nunca monta
esse invólucro:
  1. Passa `lambda p: None` como `decisor` — nunca ha veredito a converter.
  2. `hookio.executar` TAMBEM trata `SessionEnd` como evento de fim de
     sessao (`_EVENTOS_FIM_DE_TURNO_OU_SESSAO`, junto de `Stop` e
     `SubagentStop`): emite silencio ANTES de chamar o decisor. Duas
     camadas, nao uma so.

O efeito colateral real deste hook e `claims.release(dono, scope="all")`
(AC-008: "dado leases de uma sessao, quando o SessionEnd roda, entao nenhuma
lease daquela sessao permanece"). `scope="all"` (ao contrario de "turn", que
e o que `coord_stop.py` usa) libera QUALQUER claim da sessao,
independentemente de `agent_id` ou de `scope` interno (turn/session) — e o
que o docstring de `ccoord.claims.release` documenta como uso tipico de
`SessionEnd`: nao sobra lease nenhuma daquela sessao, nem a de um subagente
que ela tenha criado.

Achados 3/4 (auditoria 11/09): `<CCOORD_HOME>/own_writes/` nunca e varrido
por nada no projeto (nem TTL, nem `claims.sweep()`, que so cobre `claims/`)
— cresce um arquivo por PATH distinto ja tocado, para sempre. `coord_stop.py`
ja varre isso a cada fim de turno; este hook faz a MESMA limpeza (duplicada
de proposito, ver o comentario la) como segunda camada para sessoes que
terminam sem um `Stop` anterior — mesmo criterio (mtime > janela de eco com
margem), mesmo best-effort.
"""

from __future__ import annotations

import os
import sys
import time


def _bootstrap_src_path() -> None:
    src = os.environ.get("CCOORD_SRC")
    if not src or not os.path.isdir(os.path.join(src, "ccoord")):
        # Achado 5 (auditoria 11/09): ver coord_pre_write.py para a
        # justificativa completa -- identica nos 7 entrypoints.
        aqui = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(os.path.dirname(aqui), "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _ccoord_home() -> str:
    return os.environ.get("CCOORD_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "coord"
    )


# Mesmo criterio/valor de coord_stop.py -- ver o comentario la para a
# justificativa (bem acima da janela de eco de 15s de coord_file_changed.py).
_OWN_WRITES_TTL_S = 120


def _varrer_own_writes_expirados(home: str) -> None:
    """Duplicado de coord_stop.py de proposito (achados 3/4; sem modulo
    comum entre os 7 entrypoints — restricao da task). Remove
    `own_writes/*.json` mais velhos que `_OWN_WRITES_TTL_S` por `mtime`,
    best-effort total."""
    own_dir = os.path.join(home, "own_writes")
    try:
        nomes = os.listdir(own_dir)
    except OSError:
        return
    agora = time.time()
    for nome in nomes:
        if not nome.endswith(".json"):
            continue
        caminho = os.path.join(own_dir, nome)
        try:
            idade_s = agora - os.path.getmtime(caminho)
        except OSError:
            continue
        if idade_s > _OWN_WRITES_TTL_S:
            try:
                os.remove(caminho)
            except OSError:
                pass


def main() -> int:
    try:
        _bootstrap_src_path()
        from ccoord import hookio, claims

        payload = hookio.ler_payload()

        try:
            dono = hookio.identidade(payload)
            claims.release(dono, scope="all")
        except Exception:
            pass  # release() ja e defensivo; guarda extra, nunca propaga
        try:
            _varrer_own_writes_expirados(_ccoord_home())
        except Exception:
            pass  # limpeza best-effort; nunca pode derrubar o turno

        # SessionEnd rejeita hookSpecificOutput - decisor sempre None, e
        # hookio.executar ja degrada este evento para silencio sozinho.
        hookio.executar(payload, lambda _payload: None)
    except Exception:
        try:
            sys.stdout.write("{}\n")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
