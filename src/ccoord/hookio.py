"""ccoord.hookio - fala com o harness do Claude Code sem NUNCA quebrar o turno.

Contexto obrigatorio (ler antes de mexer):
- .specs/coordenacao-multissessao/medicao-hooks.md - payloads e formatos
  MEDIDOS em sessao real do CLI 2.1.261. E a fonte da verdade deste modulo,
  nao a documentacao publica dos hooks.
- .specs/coordenacao-multissessao/design.md secao 3.5.
- .spec/features/coordenacao-multissessao/spec.md - AC-009, AC-011, AC-013,
  AC-014.

Este modulo e a CAMADA DE BORDA: le o stdin, monta a identidade do dono,
imprime a saida no formato certo, e devolve um exit code. Tudo aqui e
defensivo por construcao - nenhuma excecao interna deste modulo (nem do
`decisor` injetado em `executar()`) pode propagar. O unico jeito de o
processo do hook sair "errado" e um bug em codigo que NAO passa por aqui.

Formatos medidos (medicao-hooks.md secao 2 e 3, design.md secao 3.5):

- Bloqueio: precisa do invólucro `hookSpecificOutput` com `hookEventName`,
  `permissionDecision":"deny"` e `permissionDecisionReason`. SEM o invólucro
  o harness ignora em silencio (e por isso o AC-011 pede a ESTRUTURA do JSON,
  nao uma substring do texto).
- Aviso ao modelo: `hookSpecificOutput.additionalContext`, e SO chega ao
  modelo, MEDIDO (secao 3, tabela grepada no transcript), em
  `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse` e
  `PostToolBatch`. `SubagentStart` NAO entra nessa lista: a tabela da secao 3
  nunca testou esse evento - ele so aparece na secao 5 como candidato
  futuro ("entra no desenho"), nao como dado medido. Ate ser medido, este
  modulo trata `SubagentStart` como sem canal confirmado (achado auditoria
  hookio #2). `Stop`/`SubagentStop` TECNICAMENTE aceitam o campo (o schema
  nao rejeita), mas medido: isso prende a sessao num laco (10 reentradas
  seguidas de `Stop`) - por isso este modulo TRATA esses dois eventos como
  se nao tivessem canal nenhum (AC-013).
- `SessionEnd` REJEITA `hookSpecificOutput` na validacao (AC-014) - nunca
  emitir o invólucro ali, nem para deny nem para contexto.
- `systemMessage` NAO chega ao modelo (vai para a tela do usuario) - este
  modulo nunca o usa para avisar o modelo.
- Limites do harness: `additionalContext` 8000 chars / 200 linhas;
  `permissionDecisionReason` 2000 chars / 20 linhas.

Regra 2 do prompt da task: a saida e SEMPRE uma unica linha de JSON valido no
stdout, nada mais - inclusive no caso de silencio, que imprime `{}` (JSON
valido, sem `hookSpecificOutput`, portanto inofensivo para qualquer evento).

Identidade do dono (AC-009): dentro de um subagente, o `session_id` do
payload e o da sessao PAI (medido) - sem compor com `agent_id`, dois
subagentes irmaos colidiriam como se fossem o mesmo dono. `identidade()`
usa `ccoord.sessions.me()` (que ja sabe ler `CLAUDE_PID`/
`CLAUDE_CODE_SESSION_ID`) so para completar `pid`/`proc_start`/`name`; quem
decide `session_id` e `agent_id` e sempre o payload (e o env como fallback
do `session_id`, para quando o payload vier incompleto).
"""

from __future__ import annotations

import json
import os
import time
# `typing` (Any/Callable/Optional, usados so em anotacoes abaixo) nao e
# importado - RNF-04/T-017, mesmo motivo de ccoord.claims/sessions/classify:
# `from __future__ import annotations` ja deixa as anotacoes como string,
# nunca avaliadas em runtime.

from ccoord import sessions
from ccoord.claims import Owner

__all__ = [
    "ler_payload",
    "identidade",
    "emitir_deny",
    "emitir_contexto",
    "emitir_silencio",
    "executar",
]

# ---------------------------------------------------------------------------
# Limites medidos (medicao-hooks.md secao 3)
# ---------------------------------------------------------------------------

_MAX_CONTEXTO_CHARS = 8000
_MAX_CONTEXTO_LINHAS = 200
_MAX_RAZAO_CHARS = 2000
_MAX_RAZAO_LINHAS = 20

# `SessionEnd` rejeita o invólucro `hookSpecificOutput` na validacao (medido,
# AC-014). E o UNICO evento em que nem deny nem contexto podem sair embrulhados.
_EVENTOS_SEM_HOOK_SPECIFIC_OUTPUT = frozenset({"SessionEnd"})

# additionalContext so chega ao modelo nestes eventos -- MEDIDO na tabela da
# secao 3 de medicao-hooks.md (grep de transcript, nao palavra do modelo).
# `Stop`/`SubagentStop` ficam de FORA de proposito: o schema deixa passar,
# mas medido, isso reabre a conversa e gera laco (AC-013) - a regra escrita
# e "nunca emitir ali", nao "emitir com cuidado".
#
# `SubagentStart` NAO entra aqui (achado auditoria hookio #2): so aparece na
# secao 5 de medicao-hooks.md ("Eventos novos que entram no desenho" --
# candidato prospectivo, ao lado de `PermissionRequest`), nunca na tabela
# medida da secao 3. Hoje isso e codigo morto (nenhum entrypoint emite em
# SubagentStart), mas incluir aqui uma linha nao medida transformaria este
# frozenset -- que a suite inteira trata como fonte de verdade sobre canais
# CONFIRMADOS -- em palpite disfarcado de medicao. Quando um hook de
# SubagentStart for construido, medir o canal de verdade (grep no transcript,
# igual secao 3) e SO ENTAO adicionar aqui.
_EVENTOS_CONTEXTO_PERMITIDO = frozenset(
    {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PostToolBatch",
    }
)

# Eventos de fim de turno/sessao: qualquer resultado do decisor (deny, warn,
# o que for) degrada para silencio aqui, ANTES de tocar em emitir_contexto/
# emitir_deny. Defesa em profundidade (regra 3 do prompt): o motivo de
# existir esta lista separada, e nao so confiar nos guardas de
# emitir_contexto/emitir_deny, e que um `decisor` poderia, por bug, pedir
# deny num Stop (sem sentido - nao ha tool call para bloquear ali) e mesmo
# assim o hook tem que sair calado, nunca "quase certo".
_EVENTOS_FIM_DE_TURNO_OU_SESSAO = frozenset({"Stop", "SubagentStop", "SessionEnd"})


# ---------------------------------------------------------------------------
# ler_payload
# ---------------------------------------------------------------------------


def ler_payload(stream: Any = None) -> dict:
    """Le o payload JSON do stdin (ou de `stream`, injetado para teste).

    Tolerante a tudo: stream vazio, lixo que nao e JSON, JSON truncado, JSON
    valido mas que nao e objeto (ex.: lista), e ate excecao ao ler o proprio
    stream (ex.: stdin fechado). Em qualquer caso desses devolve `{}` -
    nunca levanta. E o primeiro degrau da regra "o hook nunca quebra o turno".
    """
    if stream is None:
        import sys

        stream = sys.stdin

    try:
        bruto = stream.read()
    except Exception:
        return {}

    if not bruto or not bruto.strip():
        return {}

    try:
        dados = json.loads(bruto)
    except (ValueError, TypeError):
        return {}

    if not isinstance(dados, dict):
        return {}

    return dados


# ---------------------------------------------------------------------------
# identidade
# ---------------------------------------------------------------------------


def _pid_do_ambiente() -> int:
    valor = os.environ.get("CLAUDE_PID")
    if valor is None:
        return 0
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def identidade(payload: Optional[dict]) -> Owner:
    """Monta o `Owner` do dono da chamada atual.

    `session_id` vem do payload (e o da sessao PAI, mesmo dentro de um
    subagente - medido); `agent_id` tambem vem do payload, quando presente.
    E essa combinacao (nao so o `session_id`) que faz dois subagentes irmaos
    aparecerem como donos DISTINTOS (AC-009) - sem `agent_id`, a identidade
    e simplesmente a da sessao.

    `pid`/`proc_start`/`name`/`pid_domain` sao completados via
    `ccoord.sessions.me()` (que ja sabe usar `CLAUDE_PID`/
    `CLAUDE_CODE_SESSION_ID`, medidos em medicao-hooks.md secao 2, com
    fallback para procurar pelo `session_id`). Se nada for encontrado
    (registro do harness ausente/em teste), os campos ficam com defaults
    neutros (`0`, `""`) - nunca levanta por falta de registro.
    """
    payload = payload or {}
    session_id = str(payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or "")
    agent_id = payload.get("agent_id")

    pid = 0
    proc_start = ""
    name = ""
    pid_domain = ""

    try:
        sessao = sessions.me(session_id or None)
    except Exception:
        sessao = None

    if sessao is not None:
        pid = sessao.pid if sessao.pid is not None else _pid_do_ambiente()
        proc_start = sessao.proc_start or ""
        name = sessao.name or ""
        pid_domain = sessao.pid_domain or ""
    else:
        pid = _pid_do_ambiente()

    return Owner(
        session_id=session_id,
        pid=pid,
        proc_start=proc_start,
        name=name,
        agent_id=agent_id,
        pid_domain=pid_domain,
    )


# ---------------------------------------------------------------------------
# Truncamento (regra dos limites medidos)
# ---------------------------------------------------------------------------


def _truncar(texto: str, max_chars: int, max_linhas: int) -> str:
    texto = texto or ""
    linhas = texto.splitlines()
    if len(linhas) > max_linhas:
        linhas = linhas[: max_linhas - 1] + ["... (truncado, ver events.log)"]
        texto = "\n".join(linhas)
    if len(texto) > max_chars:
        texto = texto[: max_chars - 20].rstrip() + " ...(truncado)"
    return texto


# ---------------------------------------------------------------------------
# Emissao de saida - sempre UMA linha de JSON valido no stdout, nada mais
# ---------------------------------------------------------------------------


def _imprimir(obj: dict) -> None:
    """Emite o JSON do hook em ASCII puro (T-035).

    `ensure_ascii=True` NAO e detalhe de estilo aqui: o stdout do hook e um
    PIPE, e nesta maquina `sys.stdout.encoding` em pipe e **cp1252**, nao
    UTF-8. Com `ensure_ascii=False` os acentos saem como bytes cp1252
    (`sess\\xe3o`, `\\xc1rea`, travessao = `\\x97`), que nao sao UTF-8 valido --
    e TODA razao desta feature tem acento, inclusive os caminhos do vault
    ("Inteligencia de Mercado", "Area de Trabalho").

    Medido em 17/09 num experimento PAREADO (`tools/probe/run_probe5.sh`, dois
    `deny` com a MESMA razao, mudando so o `ensure_ascii`), lendo o transcript
    em vez de perguntar ao modelo:

      ensure_ascii=False -> "ENCPROBE FALSE: a sess�o home est� na �rea ..."
      ensure_ascii=True  -> "ENCPROBE TRUE: a sessao home esta na Area ..." (integro)

    Os DOIS bloquearam: o harness decodifica com substituicao, entao o JSON
    ainda e parseado e o `deny` NAO fica mudo -- a hipotese de "gate mudo por
    encoding", que eu tinha levantado como pior caso, foi **refutada pela
    medicao neste build**. O dano real e o aviso chegar ilegivel a peer, e o
    caminho de arquivo citado nele deixar de bater com o arquivo real.

    Escrita em ARQUIVO (events.log, claims) nao tem o problema: la o
    `.encode("utf-8")` e explicito. O furo e o `print()`.

    Nota de metodo: o defeito nunca nasce por descuido -- o default de
    `json.dumps` ja e `True`. Ele nasce quando alguem escreve `False` de
    proposito achando que deixa a saida mais legivel. Em hook a saida nao e
    para humano ler, e para o harness parsear.
    """
    print(json.dumps(obj, ensure_ascii=True))


def emitir_silencio() -> None:
    """Imprime `{}` - uma linha de JSON valido, sem `hookSpecificOutput`.

    E o "sai calado" medido para `Stop` (design.md 3.5): nao ha
    `additionalContext` nem `permissionDecision`, entao e inofensivo em
    QUALQUER evento, inclusive `SessionEnd` (que rejeitaria o invólucro) e
    `Stop`/`SubagentStop` (onde contexto gera laco).
    """
    _imprimir({})


def emitir_deny(evento: str, razao: str) -> None:
    """Emite o bloqueio `hookSpecificOutput{... permissionDecision:"deny"}`.

    Sem o invólucro o harness ignora em silencio (AC-011) - por isso a
    estrutura sai sempre completa quando o evento aceita o formato. O UNICO
    evento medido que rejeita o invólucro por completo e `SessionEnd`
    (AC-014); ali (e sem `evento` nenhum) degrada para `emitir_silencio()`
    em vez de arriscar um JSON que o harness recusa na validacao.
    """
    if not evento or evento in _EVENTOS_SEM_HOOK_SPECIFIC_OUTPUT:
        emitir_silencio()
        return

    razao_truncada = _truncar(razao or "", _MAX_RAZAO_CHARS, _MAX_RAZAO_LINHAS)
    _imprimir(
        {
            "hookSpecificOutput": {
                "hookEventName": evento,
                "permissionDecision": "deny",
                "permissionDecisionReason": razao_truncada,
            }
        }
    )


def emitir_contexto(evento: str, texto: str) -> None:
    """Emite o aviso `hookSpecificOutput.additionalContext`.

    So emite de fato nos eventos com canal CONFIRMADO ao modelo
    (`_EVENTOS_CONTEXTO_PERMITIDO`). `Stop`/`SubagentStop` ficam de fora por
    regra (medido: gera laco, ver AC-013); `SubagentStart` fica de fora por
    FALTA de medicao (so e citado como design prospectivo na secao 5 de
    medicao-hooks.md, nunca medido na tabela da secao 3 - achado auditoria
    hookio #2); e `SessionEnd`/qualquer outro evento fora da lista (inclusive
    `FileChanged`, que nao tem canal - ver medicao-hooks.md secao 1) degrada
    para `emitir_silencio()` - nunca um formato que o evento nao aceita
    (regra 3 do prompt da task).
    """
    if not evento or evento not in _EVENTOS_CONTEXTO_PERMITIDO:
        emitir_silencio()
        return

    texto_truncado = _truncar(texto or "", _MAX_CONTEXTO_CHARS, _MAX_CONTEXTO_LINHAS)
    _imprimir(
        {
            "hookSpecificOutput": {
                "hookEventName": evento,
                "additionalContext": texto_truncado,
            }
        }
    )


# ---------------------------------------------------------------------------
# Registro de erro (best-effort, nunca levanta) - mesma convencao de
# CCOORD_HOME usada por ccoord.claims, sem importar o helper privado de la.
# ---------------------------------------------------------------------------


def _home() -> str:
    return os.environ.get("CCOORD_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "coord"
    )


def _registrar_erro(payload: dict, evento: str, exc: Optional[BaseException]) -> None:
    try:
        home = _home()
        os.makedirs(home, exist_ok=True)
        linha = {
            "ts": int(time.time() * 1000),
            "event": "error",
            "origem": "hookio.executar",
            "hook_event_name": evento,
            "session_id": payload.get("session_id") if isinstance(payload, dict) else None,
            "erro": repr(exc) if exc is not None else "decisor_falhou",
        }
        caminho = os.path.join(home, "events.log")
        fd = os.open(caminho, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, (json.dumps(linha, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        # registrar o erro nunca pode, ele mesmo, virar um erro que escapa.
        pass


# ---------------------------------------------------------------------------
# executar - orquestracao, nunca quebra o turno
# ---------------------------------------------------------------------------


def executar(payload: Optional[dict], decisor: Callable[[dict], Any]) -> int:
    """Orquestra: chama `decisor(payload)`, emite a saida certa, devolve o exit code.

    Contrato de `decisor`: recebe o `payload` (dict) e devolve um objeto com
    `.verdict` ("allow" | "warn" | "deny") e `.reason` (str) - o formato de
    `ccoord.policy.Decision` - ou `None` quando nao ha nada a dizer. Qualquer
    excecao levantada por `decisor` e QUALQUER outra excecao interna desta
    funcao e capturada, registrada em `events.log` (best-effort) e vira
    silencio - nunca um traceback no stderr, nunca um exit diferente de 0
    (regra 1 do prompt da task: "o hook nunca derruba o turno").

    Exit code: 0 em todos os casos, EXCETO quando um `deny` e de fato
    emitido (evento aceita o invólucro) - nesse caso devolve `1`. E um sinal
    logico para quem chama/testa esta funcao; o valor que efetivamente vai
    para o processo do hook (sempre exit 0, para o harness ler o stdout) e
    decisao de quem instancia o entrypoint (fora do escopo deste modulo).

    `Stop`, `SubagentStop` e `SessionEnd` SEMPRE saem em silencio aqui,
    **antes** de sequer perguntar ao `decisor` o que fazer com o veredito:
    e a defesa em profundidade contra um `decisor` que peca (por bug) um
    contexto/deny nesses eventos - a saida so pode ser `emitir_silencio()`
    (AC-013, AC-014).
    """
    try:
        payload = payload if isinstance(payload, dict) else {}
        evento = str(payload.get("hook_event_name") or payload.get("hookEventName") or "")

        if evento in _EVENTOS_FIM_DE_TURNO_OU_SESSAO:
            emitir_silencio()
            return 0

        try:
            decisao = decisor(payload)
        except Exception as exc:  # nunca propaga o bug do decisor
            _registrar_erro(payload, evento, exc)
            emitir_silencio()
            return 0

        if decisao is None:
            emitir_silencio()
            return 0

        veredito = getattr(decisao, "verdict", None)
        razao = getattr(decisao, "reason", "") or ""

        if veredito == "deny":
            emitir_deny(evento, razao)
            return 1
        if veredito == "warn":
            emitir_contexto(evento, razao)
            return 0

        # "allow" ou veredito desconhecido: nunca bloqueia, nunca inventa formato.
        emitir_silencio()
        return 0
    except Exception as exc:  # rede de seguranca final - nada escapa desta funcao
        try:
            _registrar_erro(payload if isinstance(payload, dict) else {}, "desconhecido", exc)
        except Exception:
            pass
        try:
            emitir_silencio()
        except Exception:
            pass
        return 0
