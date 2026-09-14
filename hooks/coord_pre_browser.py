"""PreToolUse das ferramentas de browser via MCP — quem toma posse do perfil.

Fecha a ultima lacuna da feature, e justamente a colisao que a ORIGINOU: duas
sessoes disputando o mesmo perfil do Playwright custaram ao Vinicius dois
formularios de candidatura preenchidos. O gate ja recusava o KILL do processo
(AC-003), mas o claim que esse ramo consulta **nunca era adquirido por ninguem**
— os matchers do PreToolUse cobriam Edit/Write/NotebookEdit e Bash, e ferramenta
de MCP passa ao largo. Medido no ensaio T-013 (12/09): o deny do cenario 4 so
funcionou porque eu criei o claim a mao.

Duas diferencas em relacao aos outros entrypoints:

1. **Escopo `session`, nao `turn`.** O uso do browser atravessa turnos — a
   sessao navega, responde ao Vinicius, navega de novo. Claim de turno seria
   liberado no `Stop` e o perfil apareceria livre no meio do trabalho. Quem
   libera e o `browser_close` (ramo `release` aqui) ou o `SessionEnd`.
2. **Nunca recusa.** Duas sessoes no mesmo perfil nao destroem trabalho por si;
   o que destroi e a segunda tentar resolver o `Browser is already in use`
   matando o processo — e esse caminho continua recusado no ramo de kill.

Fail-open como os demais: qualquer erro sai calado com exit 0. Aqui isso e mais
delicado que o normal, porque um erro silencioso volta a deixar o perfil sem
dono e o kill sem o que consultar; por isso o teste do entrypoint exige o claim
EM DISCO, nao so a saida do hook.
"""

from __future__ import annotations

import os
import sys

_AQUI = os.path.dirname(os.path.abspath(__file__))
_SRC = os.environ.get("CCOORD_SRC") or os.path.join(os.path.dirname(_AQUI), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_TTL_BROWSER_S = 4 * 60 * 60  # sessao longa de navegacao; o SessionEnd libera antes


def _decisor_factory(hookio, sessions, claims, classify, policy):
    def _decisor(payload: dict):
        tool_name = payload.get("tool_name") or ""
        recursos = classify.classify(tool_name, payload.get("tool_input") or {}, payload.get("cwd") or "")
        recursos = [r for r in recursos if r.kind == "browser"]
        if not recursos:
            return None

        recurso = recursos[0]
        dono = hookio.identidade(payload)
        me = sessions.me()

        if recurso.action == "release":
            # `browser_close` nao mata o processo (o MCP o reaproveita), mas e a
            # declaracao de que esta sessao parou de usar. Soltar o claim aqui e
            # o que permite a peer que esperava assumir sem ninguem matar nada.
            if dono is not None:
                claims.release_resource(recurso.id, dono)
            return None

        atual = claims.owner_of(recurso.id)
        if atual is None and dono is not None:
            claims.claim(
                recurso.id,
                dono,
                ttl_s=_TTL_BROWSER_S,
                meta={"scope": "session", "purpose": f"navegando com {recurso.id.split(':')[-1]}"},
            )
            return None

        return policy.decide(recurso, atual, me, [], contexto={})

    return _decisor


def main() -> int:
    try:
        from ccoord import claims, classify, hookio, policy, sessions

        payload = hookio.ler_payload()
        decisor = _decisor_factory(hookio, sessions, claims, classify, policy)
        hookio.executar(payload, decisor)
    except Exception:  # noqa: BLE001 - hook nunca quebra o turno
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
