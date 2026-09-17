#!/usr/bin/env python
"""coord_user_prompt.py - UserPromptSubmit: libera o claim do turno ANTERIOR.

Entrypoint FINO (T-032). Faz UMA coisa e sai calado.

Por que este hook existe
------------------------
O `Stop` so libera os claims de turno quando NAO e reentrada, e nesta maquina
o `Stop` tem quatro hooks de terceiros registrados, tres deles medidos
bloqueando -- entao o turno continua e o ultimo `Stop` tambem chega com
`stop_hook_active=True`. Resultado medido em 5 dias (cruzando o `events.log`
com os transcripts, que sao a UNICA fonte com fronteira de turno): **20,1% dos
claims de turno atravessam pelo menos um prompt do usuario**, e a peer que
edita uma faixa que eu terminei no turno passado ouve "colide". Dano observado:
10 avisos falsos em 5 dias. Ver `.specs/.../medicao-fronteira-de-turno.md`.

O prompt seguinte e o unico sinal confiavel de que o turno anterior morreu --
por isso o release mora aqui, e NAO numa reentrada de `Stop` (onde ja foi
escrito errado tres vezes em 17/09: liberar quebra o turno em andamento e
encurtar o TTL libera recurso de dono vivo; as duas foram medidas piores).

Tres regras medidas que este arquivo nao pode violar
----------------------------------------------------
1. **STDOUT VAZIO.** Neste evento (e no `UserPromptExpansion`), o harness monta
   `additionalContext` SOZINHO a partir de qualquer stdout nao vazio com exit 0
   -- um `print()` esquecido vira token gasto em TODA mensagem do usuario. E
   `exit 2` BLOQUEIA o prompt ("Prompt blocked: the UserPromptSubmit hooks did
   not run over the submitted text"). Logo: nada no stdout, exit 0 sempre.
2. **`agente_exato=True`.** O release do main casa so por `session_id` quando o
   `agent_id` de quem pede e nulo, e isso apagaria tambem os claims de
   subagente (41,8% das aquisicoes de turno). Aqui pode haver subagente de
   BACKGROUND vivo, e tirar a faixa de quem esta escrevendo e silencio
   indevido. Segunda razao: este evento pode disparar DENTRO de subagente (lido
   no binario 2.1.261), onde o `session_id` e o do pai -- sem `agente_exato`,
   um subagente liberaria os claims do main.
3. **Dono indeterminavel nao libera nada.** Sem `session_id` no payload nem em
   `CLAUDE_CODE_SESSION_ID`, `identidade()` devolve um dono com `session_id`
   vazio -- que casaria com claim de dono vazio de QUALQUER sessao.

Sobre o campo `source` (ASM-007)
--------------------------------
O binario diz que este evento dispara para seis origens, e que `poll_event`
dispara NO ENQUEUE -- antes da entrega, com um turno possivelmente em
andamento. Medido em 17/09: **este build nao envia o campo** (nem `"sdk"` em
headless). Entao o filtro e escrito para o futuro sem depender do presente: so
recusa quando o campo VEM e e `poll_event`. Com o campo ausente -- que e o
estado de hoje -- o hook funciona normalmente.

Nao filtro `system` (mensagem de peer, notificacao de tarefa): medido no
transcript que essas entradas viram turno PROPRIO, com `promptId` novo e
`Stop` proprio, sequencial -- ou seja, quando uma delas chega o turno anterior
ja terminou de verdade, e liberar ali esta certo.
"""

from __future__ import annotations

import json
import os
import sys
import time

# Origens que NAO significam "o turno anterior acabou". Hoje o campo nem vem no
# payload; isto e defesa para quando vier. Manter curta e justificada: cada
# nome aqui e um caso em que o release seria feito no meio de um turno vivo.
_ORIGENS_QUE_NAO_ENCERRAM_TURNO = frozenset({"poll_event"})


def _registrar_erro_local(exc: BaseException) -> None:
    """Grava uma linha de erro no `events.log` SEM depender do pacote `ccoord`.

    Por que nao usar `hookio._registrar_erro`, que ja existe: o cenario que a
    auditoria provou (17/09) e justamente `CCOORD_SRC` quebrado -- e ai o
    `import ccoord` falhou e nao ha `hookio` para chamar. Telemetria que
    depende do que pode ter quebrado nao e telemetria.

    O sintoma sem isto era o pior possivel: o hook saia com rc 0, stdout vazio,
    o claim sobrevivia e **nenhuma linha em lugar nenhum** -- silencio
    indistinguivel de "rodou e nao havia nada a fazer". Este era o unico dos 9
    entrypoints sem registro no fail-open.

    Best-effort e mudo por definicao: se ATE isto falhar, engole. E escreve em
    ARQUIVO com `.encode("utf-8")` explicito, nunca no stdout -- neste evento
    qualquer stdout nao vazio vira `additionalContext` no contexto do modelo.
    """
    try:
        home = os.environ.get("CCOORD_HOME") or os.path.join(
            os.path.expanduser("~"), ".claude", "coord"
        )
        os.makedirs(home, exist_ok=True)
        linha = {
            "ts": int(time.time() * 1000),
            "event": "error",
            "origem": "coord_user_prompt",
            "hook_event_name": "UserPromptSubmit",
            # QUEM quebrou. Sem isto, N linhas identicas de erro nao distinguem
            # "uma sessao quebrada x 40 prompts" de "40 sessoes quebradas" -- e
            # nesta maquina ha varias sessoes simultaneas, que e o problema que
            # esta feature inteira existe para tratar. O env serve mesmo quando
            # o `import ccoord` falhou, que e justamente o cenario coberto aqui;
            # nao da para usar `hookio.identidade` pelo mesmo motivo.
            "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID") or None,
            "erro": repr(exc),
        }
        caminho = os.path.join(home, "events.log")
        fd = os.open(caminho, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, (json.dumps(linha, ensure_ascii=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass


def _bootstrap_src_path() -> None:
    src = os.environ.get("CCOORD_SRC")
    if not src or not os.path.isdir(os.path.join(src, "ccoord")):
        # Achado 5 (auditoria 11/09): ver coord_pre_write.py para a
        # justificativa completa -- identica nos 8 entrypoints.
        aqui = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(os.path.dirname(aqui), "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def main() -> int:
    try:
        _bootstrap_src_path()
        from ccoord import hookio, claims

        payload = hookio.ler_payload()

        origem = payload.get("source")
        if isinstance(origem, str) and origem in _ORIGENS_QUE_NAO_ENCERRAM_TURNO:
            return 0

        dono = hookio.identidade(payload)
        if not dono.session_id:
            # Regra 3: dono vazio casaria com claim de dono vazio de outra
            # sessao. Nao liberar e sempre seguro; liberar o alheio nao e.
            return 0

        claims.release(dono, scope="turn", agente_exato=True)
    except Exception as exc:
        # Fail-open (RNF-02): o prompt do usuario nunca para por causa deste
        # hook -- mas fail-open CALADO esconde o defeito (achado da auditoria
        # de 17/09). Silencio na SAIDA, registro no events.log: quem investiga
        # depois precisa distinguir "rodou e nao havia o que liberar" de
        # "quebrou e ninguem soube".
        _registrar_erro_local(exc)
    return 0


if __name__ == "__main__":
    # Sem `sys.exit(main())` com valor != 0 em nenhum caminho: exit 2 bloqueia
    # o prompt e outros codigos sujam a tela do usuario.
    sys.exit(main())
