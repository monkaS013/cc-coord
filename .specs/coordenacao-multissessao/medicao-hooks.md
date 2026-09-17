# Task 1 — Medição dos eventos de hook (payloads reais)

**Data:** 2026-09-11 · **CLI:** v2.1.261 (`bin/claude.exe`, npm global) · **Máquina:** Windows 11
**Método:** dois níveis de evidência, nenhum resumo de IA.
1. **Estático** — leitura das strings do próprio `claude.exe` (schemas zod, doc embutida dos hooks, código do watcher).
2. **Runtime** — 4 sessões headless (`claude -p --model haiku --settings <probe> --debug-file`) com um hook de captura em cada evento, gravando o stdin literal. Sandbox: `tools/probe/`, `--settings` próprio. **Nada em `~/.claude/` foi alterado.**

Artefatos: `tools/probe/capture.py`, `probe-settings.json`, `run_probe{,2,3,4}.sh`, `debug*.log`.

---

## 1. Resposta à Q-004 — `FileChanged` existe, dispara, mas **não fala com a sessão**

| Pergunta | Resposta | Evidência |
|---|---|---|
| O evento existe? | **Sim**, na lista oficial de 33 eventos do binário | `zo()` no `claude.exe`: `{PreToolUse, …, CwdChanged, FileChanged, DirectoryAdded, MessageDisplay}` |
| Dispara para alteração feita por **outro processo**? | **Sim** | Rodada 2: `echo >> alvo.txt` de um bash externo enquanto a sessão estava presa num `sleep 18` → `20:12:55.360 FileChanged: change …alvo.txt` + payload capturado |
| Latência | **0,62 s** | echo às 20:12:54.739 → evento às 20:12:55.360. Bate com `awaitWriteFinish:{stabilityThreshold:500, pollInterval:200}` |
| Dispara também para escrita da **própria** sessão? | **Sim** | Rodada 4: o próprio Bash da sessão escreveu no arquivo e o evento disparou igual |
| O retorno do hook chega ao **modelo**? | **Não** | Rodadas 2 e 4: `ADDCTX-FileChanged` e `SYSMSG-FileChanged` **não aparecem no transcript JSONL**, embora o hook tenha rodado com status 0 e a saída tenha sido parseada |

**Por que não chega** (código do watcher, literal):

```js
TE.watch(paths,{persistent:!0,ignoreInitial:!0,awaitWriteFinish:{stabilityThreshold:500,pollInterval:200},ignorePermissionErrors:!0})
// no evento:
Jhn(...).then(({results,watchPaths,systemMessages})=>{ if(watchPaths.length>0) re(watchPaths);
   for(let Ne of systemMessages) v?.(Ne,!1);
   for(let Ne of results) if(!Ne.succeeded&&Ne.output) v?.(Ne.output,!0) })
```

Do retorno do `FileChanged` só são consumidos **`watchPaths`** (atualiza a lista vigiada) e **`systemMessages`** (tela do usuário). `additionalContext` é descartado.

### Consequência para o design — o design **não cai**, ganha um sensor

`FileChanged` **não** vira a peça central (era a hipótese do RETOMAR.md). Ele é **sensor**, não canal:
- serve para **detectar em 0,6 s** que um arquivo com claim meu mudou em disco, e **gravar isso em `~/.claude/coord/`**;
- o **aviso ao modelo** sai no próximo `PreToolUse`/`PostToolBatch`, que são os eventos com canal de volta;
- como dispara também para a escrita **própria**, o hook precisa **filtrar o eco**: comparar `file_path` + mtime com o último write conhecido da própria sessão, senão o sensor avisa sobre si mesmo;
- o **registro de claims continua necessário** — `FileChanged` diz *que* mudou, nunca *quem* mudou (o payload não tem autor).

**Limites medidos:** o watcher só sobe se existir hook `FileChanged` ou `CwdChanged` configurado; `matcher` são **nomes de arquivo no cwd** (ex.: `.envrc|.env`), paths absolutos entram por `hookSpecificOutput.watchPaths` de `SessionStart`/`CwdChanged`/`FileChanged`; **path UNC remoto é descartado** (`FileChanged: dropped remote UNC watch path(s)`) — relevante para vault/OneDrive em share de rede.

---

## 2. Payloads reais (capturados, não documentados)

Campos comuns a todos: `session_id`, `transcript_path`, `cwd`, e — quando já houve input — `prompt_id`, `permission_mode`.

```jsonc
// PreToolUse
{"session_id","transcript_path","cwd","prompt_id","permission_mode",
 "hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{...},"tool_use_id":"toolu_…"}

// PreToolUse DENTRO de subagente  (AC-009 confirmado)
{… ,"agent_id":"a780cfefa2bf231c7","agent_type":"general-purpose","effort":{"level":"high"}, …}
//  session_id é o MESMO do pai → a identidade do dono precisa ser (session_id, agent_id)

// SessionStart   {"…","hook_event_name":"SessionStart","source":"startup"}
// SessionEnd     {"…","hook_event_name":"SessionEnd","reason":"other"}
// FileChanged    {"…","hook_event_name":"FileChanged","file_path":"C:\\…\\alvo.txt","event":"change"}
//                (event ∈ change|add|unlink; SEM permission_mode, SEM autor)
// Stop           {"…","hook_event_name":"Stop","stop_hook_active":false,
//                 "last_assistant_message":"…","background_tasks":[],"session_crons":[]}
// PostToolBatch  {"…","tool_calls":[{"tool_name","tool_input","tool_use_id","tool_response"}]}
// SubagentStart  {"…","agent_id","agent_type","hook_event_name":"SubagentStart"}
```

**Variáveis de ambiente entregues ao hook** (novidade útil, dispensa consulta a arquivo):
`CLAUDE_PID`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_PROJECT_DIR`, `CLAUDE_CODE_MESSAGING_SOCKET`
(`\\.\pipe\LOCAL\cc-msg-<hash>`), `CLAUDE_CODE_MESSAGING_TOKEN`, `CLAUDE_ENV_FILE`
(só em `SessionStart`/`Setup`/`CwdChanged`/`FileChanged`).

---

## 3. Canal de volta, por evento — medido no transcript, não pela palavra do modelo

| Evento | `additionalContext` chega ao modelo? | Observação |
|---|---|---|
| `SessionStart` | **Sim** | confirmado no transcript (o modelo não o listou quando perguntado — testemunha fraca) |
| `UserPromptSubmit` | **Sim** | |
| `PreToolUse` | **Sim** | e `permissionDecision:"deny"` + `permissionDecisionReason` chega **literal** ao modelo (medido: `DENY-PROBE: recurso em uso pela sessao teste-peer`) |
| `PostToolUse` | **Sim** | |
| `PostToolBatch` | **Sim** | uma injeção por lote de tool calls |
| `Stop` / `SubagentStop` | **Sim, e é armadilha** | ver §4 |
| `FileChanged` | **Não** | §1 |
| `SessionEnd` | **Não** | **rejeita `hookSpecificOutput`** na validação: *"expected one of PreToolUse \| UserPromptSubmit \| …"*. Serve para liberar claim, não para avisar |
| `systemMessage` (qualquer evento) | **Não** | descrição literal do schema: *"Warning message shown to the user"*. Não apareceu na resposta do modelo em nenhuma rodada |

**Limites de tamanho** (do binário): `additionalContext` 8000 chars / 200 linhas · `permissionDecisionReason` 2000 / 20 linhas · `systemMessage` 4000 / 20 · `reason` 2000 / 20.

### 🔴 O `deny` sai com **exit 0** — exit 1 deixa a ação PASSAR

Doc literal do `PreToolUse` no binário: *"Exit code 0 – stdout/stderr not shown. Exit code 2 – show
stderr to model and block tool call. **Other exit codes – show stderr to user only but continue with
tool call**"*. Ou seja, **exit 1 cai em "continue with tool call"**: o bloqueio some e sobra uma
mensagem de erro na tela do usuário. Quem bloqueia é o JSON (`permissionDecision: "deny"`), e ele só
é lido no caminho de sucesso. Medido na rodada 3: o hook devolveu o JSON de deny com `sys.exit(0)` e
a ação **foi bloqueada**, com a razão chegando literal ao modelo.

**Regra para os entrypoints:** processo de hook termina em **0**, sempre. Um código de retorno
interno que sinalize "neguei" pode existir dentro do módulo, mas **não pode virar o exit code do
processo** — é a diferença entre o gate funcionar e o gate parecer que funciona.

---

## 4. Três pegadinhas medidas que a implementação não pode errar

1. **`additionalContext` em `Stop` prende a sessão em loop.** Rodada 1: o hook `Stop` devolveu `additionalContext` e o `Stop` disparou **10 vezes** seguidas. O schema explica: *"Feedback for the model; **the conversation continues** so the model can act on it"*. O `coord_stop.py` (libera claims de turno) **tem de sair calado** — só exit 0. Existe `stop_hook_active` no payload para detectar reentrada.
2. **`SessionEnd` não aceita `hookSpecificOutput`** — emitir o wrapper ali gera erro visível ao usuário e nada mais.
3. **O modelo é testemunha não confiável do que recebeu.** Ele deixou de listar `ADDCTX-SessionStart` e `ADDCTX-UserPromptSubmit` que o transcript prova terem chegado. Toda verificação de injeção nesta feature se faz **grepando o transcript JSONL**, não perguntando à sessão.

---

## 5. Eventos novos que entram no desenho

| Evento | Uso na feature |
|---|---|
| `FileChanged` | sensor de alteração em arquivo com claim (0,6 s), grava em `coord/`; **não** avisa sozinho |
| `PostToolBatch` | ponto barato de aviso agregado: uma injeção por lote, em vez de uma por tool call |
| `SubagentStart` | injeta `additionalContext` **no subagente** e entrega `agent_id` — é onde o subagente recebe o mapa de peers |
| `PermissionRequest` | decisão allow/deny quando o diálogo aparece — 2ª linha de defesa, fora do escopo do v1 |
| `Setup` | `trigger: init|maintenance` — candidato para o `ccoord install` |
| `ConfigChange` | dispara quando `settings.json`/skills mudam em sessão — **detecta a peer mexendo na minha configuração** |

---

## 5-bis. `UserPromptSubmit` — quatro fatos lidos no binário (17/09, build 2.1.261)

⚠️ **Lido no binário, NÃO medido em runtime.** Vale como aviso e como roteiro de medição (T-030), não
como prova. O que o binário diz e o que o harness faz já divergiram nesta feature.

Origem: `bin/claude.exe` (209 MB, bun compilado), varredura por contexto ASCII ao redor de
`UserPromptSubmit`.

### 1. O evento dispara para seis origens diferentes, e nem todas são fim de turno

O payload tem um campo `source`, com esta descrição literal:

> `user` = submitted from the interactive composer · `sdk` = non-interactive entrypoint (`-p` / Agent
> SDK) · `loop_wakeup` = dynamic /loop wakeup · `schedule_wakeup` = scheduled-task fire
> (CronCreate/routine) · `system` = other machine-injected turns (**peer/channel messages**, task
> notifications, auto-continuation) · `poll_event` = the poll-event channel enqueue-time pass (**the
> hook fires when the host submits an event, before its delivery ack exists**)
>
> *"Payloads may omit it while the field rolls out."*

Duas dessas origens derrubam a premissa "UserPromptSubmit = o turno anterior acabou":

- **`poll_event` dispara no ENQUEUE**, dito com todas as letras — ou seja, com um turno possivelmente
  em andamento;
- **`system` inclui mensagem de peer** (`SendMessage`, que esta feature usa como canal!) e
  notificação de tarefa. Se uma peer me escrever no meio do meu turno e isso gerar um
  `UserPromptSubmit`, um release ali cai **dentro de um turno vivo** — que é exatamente o erro das
  três tentativas de 17/09, com outra roupa.

Consequência para o desenho: liberar só com `source == "user"`. O erro é assimétrico — não liberar num
wakeup legítimo custa esperar o próximo prompt (status quo); liberar no meio de um turno vivo destrói
faixa de quem está escrevendo. **Mas o campo é opcional e está em rollout**: se este build não o
enviar no composer interativo, a regra torna o hook inerte. É a primeira coisa que a T-030 mede.

### 2. O evento PODE disparar dentro de subagente

Existem dois produtores. Um ignora agente; o outro resolve o alvo como `r.agentId ?? r.session.id` e
monta o payload com `agent_id` preenchido (o payload base dos hooks inclui `session_id`,
`transcript_path`, `cwd`, `scratchpad_dir`, `prompt_id`, `permission_mode`, `agent_id`, `agent_type`,
`effort`). Como o `session_id` dentro de subagente é o da sessão **pai** (já medido nesta feature), um
release que casasse só por `session_id` mataria os claims do main a partir do subagente.

A decisão de 17/09 (`agente_exato=True`, poupar subagente) já cobre isso — mas por sorte, não por
projeto. Agora está escrito.

### 3. Stdout não vazio com exit 0 vira `additionalContext` automaticamente

Literal do binário: com `status === 0`, se a saída aparada não for vazia, o harness monta
`{hookSpecificOutput: {hookEventName, additionalContext: <stdout>}}` sozinho — só para
`UserPromptSubmit` e `UserPromptExpansion`.

Ou seja: **qualquer coisa que o hook imprima é injetada no contexto do modelo a cada prompt.** Um
`print()` de depuração esquecido vira token gasto em toda mensagem do usuário. O hook tem de sair com
stdout **absolutamente vazio** (AC-026).

E `status === 2` **bloqueia o prompt** (mensagem: *"Prompt blocked: the UserPromptSubmit hooks did not
run over the submitted text"*). A regra dos entrypoints — sair sempre com 0 — vale aqui em dobro.

### 4. Timeout default é 30 s

Mapa de timeouts do binário: `PreToolUse: 15`, `UserPromptSubmit: 30`, `Stop: 120`. Os hooks do
cc-coord declaram `timeout: 10` explicitamente, o que continua valendo.

---

## 6. O que esta medição **não** cobriu

- `FileChanged` com **muitos** paths vigiados (custo do watcher com ~100 arquivos) — medir na Task de performance.
- Comportamento em sessão **interativa** (as 4 rodadas foram headless `-p`): `systemMessage` provavelmente aparece na tela, o que não muda o desenho, mas muda a UX do aviso.
- `crossSessionInbound` sob `bypassPermissions` (Q-003, herdada).
