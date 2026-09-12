# Coordenação entre sessões simultâneas — Design

**Spec:** `.specs/coordenacao-multissessao/spec.md`
**Fase:** Design (tlc-spec-driven)
**Data:** 2026-09-11
**Status:** Draft — aguarda aprovação do Vinicius antes de Tasks

---

## 0. O que mudou desde a Specify (04/09) e por quê

A fase Specify desenhou um **gate mecânico** como peça central. A pesquisa de 11/09 (3 frentes) e a
medição das sessões vivas **invertem a ênfase**: o v1 é **informativo**, e o `deny` fica reservado ao
irreversível. Registro das mudanças, com a evidência de cada uma:

| # | Mudança | Evidência |
|---|---|---|
| D-01 | **Não construir registro de sessões.** O harness já mantém `~/.claude/sessions/<pid>.json` com `name`, `cwd`, `status`, `updatedAt`, `messagingSocketPath`, `peerFeatures`, `pidDomain`, `procStart` | Medido em 11/09 nesta máquina (v2.1.261). Resolve RF-06 sem código de registro |
| D-02 | **Unidade de coordenação = (arquivo, faixa de linha)**, não o arquivo | Relato da peer `home`: ela e a `home-joyful-comet` dividiram `web/app.js` (~1620-1690 vs ~5400-5560) sem 1 conflito. Lease por path inteiro recusaria em massa onde não há colisão |
| D-03 | **Aviso é a peça principal; `deny` só no irreversível** | Escolha do Vinicius (11/09) + prior art: file-claim **advisory com TTL** é o padrão 2026 (MCP Agent Mail; AgentRoom arXiv 2608.23740). Critério dele segue "zero retrabalho", e atrito que não evita perda é defeito (spec §2) |
| D-04 | **Hook é obrigatório — convenção em CLAUDE.md/memória não resolve** | Issue `anthropics/claude-code` #88862: time com 3-6 sessões num clone no Windows mediu **37% dos commits colidindo**; conclusão do autor é que nenhuma solução em userland impede a sessão de esquecer a convenção. É a formulação técnica da cobrança do Vinicius ("se eu não falar, você não verifica") |
| D-05 | **Lease com TTL > lock puro** | Chubby/etcd/Consul: a expiração automática cura deadlock por design, sem precisar detectar "o dono morreu" |
| D-06 | **Staleness = PID + `procStart`**, nunca PID sozinho | PID sofre reuso. O registro do harness já entrega `procStart`, o que dispensa consultar WMI no caminho quente |
| D-07 | **Não copiar `parallel-sessions`** (6★, 959 testes, o único projeto dedicado a isto) | Depende de `flock`, `ps -o lstart=`, `inotifywait` — **não roda no Windows**. Serve de referência de desenho (staleness por consenso de 3 sinais), não de código |

**Achados que NÃO alteram o desenho, mas entram como guarda:**

- `status` do registro **não é tempo real**: medida em 11/09 mostrou peer com `status: "busy"` e
  `updatedAt` de **262 s**, enquanto outra atualizava a cada ~40 s. Vocabulário tem ao menos 3 valores
  (`busy`, `idle`, `waiting`). Comparar com `== "idle"` classifica errado.
- **Peer cita sessão morta de memória.** As duas peers afirmaram que `home-joyful-comet` estava viva;
  o registro e o `ListAgents` diziam 2 peers. O registro em disco é a verdade, a palavra do peer é pista.
- **Coordenador morto ≠ recurso órfão.** Inferi que o lock do Playwright da sessão morta estava
  abandonado; medido, havia 10 `chrome.exe` do perfil com o mais novo criado minutos antes = em uso.
  Só o **recurso** diz se o recurso está livre.
- **Critério de órfão do browser (confirmado pela peer `home` em 11/09, dona dos processos):** o MCP
  **mantém o processo vivo depois do `browser_close`**, para reaproveitar na próxima chamada. Logo
  *presença de processo* não prova uso, e *ausência de sessão desenhando* não prova abandono. O par de
  sinais que decide é **idade alta + ninguém conseguindo abrir** (`Browser is already in use` em quem
  tenta) = órfão; **processo recente** = uso. Foi essa a distinção que autorizou a limpeza de 9
  processos presos havia ~2h40 mais cedo no mesmo dia — com pergunta explícita ao Vinicius antes.
  **Implicação para `policy.py`:** o ramo de kill NUNCA decide sozinho por idade; ele emite `deny` e
  manda perguntar ao Vinicius. Autorização de kill é dele e não se pede a peer (permission laundering).

---

## 1. Arquitetura

```mermaid
graph TD
    H1[SessionStart] --> S[sessions.py<br/>lê ~/.claude/sessions/*.json]
    H2[PreToolUse Edit/Write] --> C[classify.py]
    H3[PreToolUse Bash] --> C
    H4[Stop / SessionEnd] --> R[claims.py release]
    C --> CL[claims.py<br/>~/.claude/coord/claims/]
    C --> S
    CL --> P[policy.py]
    S --> P
    P --> IO[hookio.py<br/>additionalContext | deny]
    IO --> H1
    IO --> H2
    IO --> H3
    CLI[ccoord CLI<br/>status/who/release/sweep] --> CL
    CLI --> S
```

**Fluxo em uma frase:** todo `Edit`/`Write`/`Bash` passa por um hook que descobre as peers pelo
registro do harness, cruza com os claims em disco e devolve **contexto** (quase sempre) ou **deny**
(kill e git write misturado).

**Princípio de custo:** o caminho quente (`PreToolUse`) só lê arquivos pequenos em `~/.claude/`.
Nenhuma chamada a WMI, a `git` ou a rede no caminho quente, exceto no ramo git, que é raro.

---

## 2. Reuso

| O que já existe | Onde | Como uso |
|---|---|---|
| Registro de sessões vivas | `~/.claude/sessions/<pid>.json` (harness) | Fonte única de peers. **Não replicar** |
| Canal de mensagem | `ListAgents` + `SendMessage` nativos | A política manda anunciar; o código não implementa transporte |
| Padrão de hook Python no Windows | `~/.claude/hooks/verify_gate.py`, `context_alert.py` | Mesmo estilo de entrypoint, `shell: "bash"` **confirmado funcional** (Git Bash Cygwin 5.3.9, heartbeat ativo em 11/09) |
| Estado de hook por sessão | `~/.claude/hooks/state/` | Mesma convenção de nomeação por `session_id` |
| Política escrita carregada sempre | `~/.claude/rules/*.md` via `@` no CLAUDE.md | `coordenacao-sessoes.md` entra igual a `deploy-easypanel.md` |
| Wrapper que nomeia sessões | `$PROFILE.CurrentUserAllHosts` (04/09) | Já resolve ASM-005; nomes reconhecíveis nas mensagens |

---

## 3. Componentes

### 3.1 `ccoord/sessions.py`
- **Propósito:** descobrir sessões vivas e responder "quem é peer e o que ela está fazendo".
- **Interfaces:**
  - `me(session_id: str) -> Session | None`
  - `peers(exclude_pid: int) -> list[Session]` — só vivas
  - `is_alive(s: Session) -> bool` — `pid` existe **e** `procStart` casa **e** `updatedAt` dentro do TTL
- **Dependências:** stdlib (`json`, `os`, `glob`, `time`); `pidDomain` para ignorar sessões de outro host.
- **Guardas:** leitura defensiva (ASM-003) — arquivo pode estar em escrita parcial; `try/except` por
  arquivo, nunca falhar o conjunto por um arquivo ruim. `status` é **rótulo**, não decisão.

### 3.2 `ccoord/claims.py`
- **Propósito:** claims advisory (arquivo + faixa) e leases de recurso, com TTL e sweep.
- **Interfaces:**
  - `claim(resource: str, owner: Owner, ttl_s: int, meta: dict) -> ClaimResult`
  - `owner_of(resource: str) -> Claim | None`
  - `overlapping(path: str, lines: tuple[int,int] | None) -> list[Claim]`
  - `release(owner: Owner, scope: Literal["turn","session","all"]) -> int`
  - `sweep() -> int` — remove claims de dono morto, loga em `events.log`
- **Primitiva de exclusão:** `os.open(path, O_CREAT|O_EXCL|O_WRONLY)`.
  **Proibido tmp+rename** (RNF-06: no Windows `rename()` dá `EPERM` com handle aberto no destino — foi
  a raiz da corrupção do `~/.claude.json`). `mkdir` atômico é a alternativa aceita se `O_EXCL` der
  problema em rede.
- **TTL:** claim de arquivo = 15 min (escopo turn); lease de recurso = 60 min (escopo session), com
  jitter de 10-20% na renovação. Dono morto vence TTL: liveness por PID+`procStart` é o sinal forte.

### 3.3 `ccoord/classify.py`
- **Propósito:** `(tool_name, tool_input, cwd) -> list[Resource]`. Puro, sem I/O — é o que fica fácil de testar.
- **Regras que não podem ser erradas:**
  - `Edit|Write|NotebookEdit` → chavear em **`tool_input.file_path`**, NUNCA em `cwd`
    (issue #76727: 29% das escritas eram `cwd` no checkout primário escrevendo em worktree por path
    absoluto; gate por `cwd` negaria 4.010 chamadas em silêncio).
  - Faixa de linha do `Edit`: derivada do `old_string` **depois** de localizá-lo no arquivo; se não
    localizar, claim do arquivo inteiro com flag `range: null` (degrada para o comportamento antigo).
  - `Write` → sempre arquivo inteiro (é reescrita), o que já explica por que `Write` é mais perigoso
    que `Edit` em árvore compartilhada.
  - `Bash` → detectar: kill/Stop-Process, `git commit|push|reset|checkout`, bind de porta, `prisma migrate`.

### 3.4 `ccoord/policy.py`
- **Propósito:** `(resource, owner, me, peers) -> Decision`. Também puro.
- **Tabela de decisão (v1, conforme escolha do Vinicius em 11/09):**

| Situação | Decisão | Razão emitida |
|---|---|---|
| `Edit`/`Write` em arquivo com claim de peer viva, **faixas se sobrepõem** | `warn` (additionalContext) | nomeia a peer, a faixa dela e manda `SendMessage` antes de editar |
| `Edit`/`Write`, mesmo arquivo, **faixas disjuntas** | `warn` curto | só informa; é o caso que funcionou hoje |
| `Write` (arquivo inteiro) em arquivo com claim de peer | `warn` **forte** | reescrita apaga o que entrou na janela — sugere `Edit` cirúrgico |
| Kill de processo casando perfil de browser/servidor de peer | **`deny`** | nomeia dono, proíbe kill, prescreve alternativa (`playwright-b`, `SendMessage`+`notify_when_idle`) |
| `git commit`/`push` com peer viva no mesmo repo | **`deny`** | manda rodar `git log origin/<br>..HEAD` e `git status --short` (2 colunas), e declarar mistura ao Vinicius |
| Bind em porta com lease de peer | `warn` | sugere porta livre concreta |
| Migração de schema com peer viva no repo | `warn` forte | adiar schema, implementar só o que não toca |

- **RF-04 (recusa prescritiva):** toda `deny` nomeia o comando ou caminho alternativo. Recusa seca gera contorno.

### 3.5 `ccoord/hookio.py`
- **Propósito:** ler stdin JSON, emitir saída no formato certo, nunca quebrar o turno.
- **Formato de bloqueio** (o wrapper é obrigatório; sem ele é **no-op silencioso**):
  ```json
  {"hookSpecificOutput":{"hookEventName":"PreToolUse",
   "permissionDecision":"deny","permissionDecisionReason":"..."}}
  ```
- **Formato de injeção:** `hookSpecificOutput.additionalContext` (precisa estar aninhado).
- **Identidade do dono:** `session_id` do payload é o **da sessão pai** dentro de subagente →
  compor com `agent_id` quando presente (AC-009).
- ✅ **Verificação feita em 11/09 (Task 1)** — payloads capturados de sessão real, ver
  [`medicao-hooks.md`](medicao-hooks.md). Confirmados: base `{session_id, transcript_path, cwd,
  prompt_id, permission_mode}`; `PreToolUse` traz `tool_name`/`tool_input`/`tool_use_id`; dentro de
  subagente vêm `agent_id` + `agent_type` com o **`session_id` do pai** (AC-009 procede); `deny` com
  `permissionDecisionReason` chega **literal** ao modelo (AC-011 procede).
- ⚠️ **Três regras que saíram da medição:**
  1. `coord_stop.py` **sai calado** (exit 0, sem `hookSpecificOutput`): `additionalContext` em `Stop`
     faz a conversa continuar — medido, 10 disparos de `Stop` em loop. Checar `stop_hook_active`.
  2. `coord_session_end.py` **não emite `hookSpecificOutput`**: `SessionEnd` rejeita o wrapper na
     validação. Só libera claims e sai.
  3. `systemMessage` vai para a **tela do usuário**, nunca para o modelo. Aviso ao modelo é sempre
     `additionalContext` (8000 chars / 200 linhas) ou `permissionDecisionReason` (2000 / 20).
- **Identidade sem ler arquivo:** o hook recebe `CLAUDE_PID`, `CLAUDE_CODE_SESSION_ID` e
  `CLAUDE_CODE_MESSAGING_SOCKET` por variável de ambiente.

### 3.6 `ccoord/cli.py`
`ccoord status | who <recurso> | release --mine | sweep` — RF-08. Cobre a visibilidade sem mexer no
`statusline.py` do monitor de uso (fora de escopo, spec §9).

### 3.7 Entrypoints em `~/.claude/hooks/`
Finos, só importam `ccoord`: `coord_session_start.py`, `coord_pre_write.py`, `coord_pre_bash.py`,
`coord_stop.py`, `coord_session_end.py`.

### 3.8 `FileChanged` — MEDIDO em 11/09 (Task 1): sensor, não canal

Resultado completo em [`medicao-hooks.md`](medicao-hooks.md). Resumo do que muda aqui:

- **Existe e dispara** para alteração feita por **outro processo**, em **0,62 s** (chokidar com
  `awaitWriteFinish` 500 ms). Também dispara para a escrita da **própria** sessão → **filtrar o eco**.
- **Não fala com a sessão:** do retorno do hook o harness só consome `watchPaths` e `systemMessages`
  (tela do usuário). `additionalContext` é descartado — provado por ausência no transcript JSONL.
- **Não diz quem alterou.** O payload é `{file_path, event}`. O registro de claims continua sendo o
  que responde "de quem é".

Logo o `FileChanged` entra como **`coord_file_changed.py`**: detecta, carimba em
`~/.claude/coord/changed/`, e o aviso ao modelo sai no **próximo `PreToolUse`/`PostToolBatch`**, que
têm canal de volta. Ele **não** vira a peça central — o desenho de claims permanece inteiro.

---

## 4. Modelos de dados

```python
# ~/.claude/coord/claims/<slug>.json
{
  "resource": "file:C--dev-dashboard-inteligencia-mercado-web-app.js",
  "path": "C:\\Users\\...\\web\\app.js",
  "range": [6556, 6574],          # null = arquivo inteiro
  "owner": {"session_id": "...", "agent_id": null, "pid": 24948,
            "proc_start": "134336295818640398", "name": "home-distributed-snail",
            "pid_domain": "win32:laptop-q3ai3ek1"},
  "scope": "turn",                 # turn | session
  "purpose": "ajuste da legenda de marcas",
  "acquired_at": 1789156028950,
  "renewed_at": 1789156028950,
  "ttl_s": 900
}
```

`events.log` (append-only, uma linha JSON por evento): `acquire`, `deny`, `warn`, `steal_stale`,
`release`, `error`. É o que permite auditar depois se o sistema evitou ou causou retrabalho.

---

## 5. Erros

| Cenário | Tratamento | Efeito |
|---|---|---|
| `~/.claude/coord/` ausente/corrompido | cria; se falhar, **fail-open** + `events.log` | turno segue (RNF-02) |
| Idem, mas a ação é **kill** | **fail-closed**: `deny` | não matar é sempre seguro (RNF-03) |
| `sessions/*.json` em escrita parcial | ignora aquele arquivo | peer some da lista por 1 leitura |
| Hook estoura o tempo | exit 0 | nunca trava o turno (RNF-04: <150 ms) |
| Claim de dono morto | `sweep()` remove e loga `steal_stale` | recurso volta a ficar livre |
| Peer em outro `pidDomain` | ignorada | WSL×Windows não se alcançam (spec §9) |

---

## 6. Decisões técnicas

| Decisão | Escolha | Razão |
|---|---|---|
| Registro de sessões | **Não construir** | Harness já entrega (D-01) |
| Exclusão mútua | `O_CREAT\|O_EXCL` | `rename` no Windows dá `EPERM` (RNF-06) |
| Expiração | Lease com TTL + liveness PID+`procStart` | TTL cura deadlock; PID sozinho sofre reuso (D-05, D-06) |
| Granularidade | (arquivo, faixa de linha) | D-02 |
| Postura padrão | Avisar; `deny` só no irreversível | Escolha do Vinicius + D-03 |
| Linguagem | Python 3.14 stdlib (`C:\Python314`) | RNF-01 |
| Shell do hook | `bash` (Git Bash) | **Confirmado 11/09**: Cygwin 5.3.9 real, heartbeat do `verify_gate` ativo. A pegadinha "bash no Windows = launcher vazio do WSL" **não se aplica aqui** |
| Isolamento | Worktree por sessão continua valendo | Padrão dominante 2026 (Cursor, JetBrains, agent teams). Limite: em PowerShell o harness aplica só a checagem de working-directory |

---

## 7. Perguntas abertas desta fase

- ~~**Q-004** `FileChanged` existe e dispara para alteração feita por outra sessão?~~ **RESOLVIDA em
  11/09 (Task 1):** existe, dispara em 0,62 s para escrita externa — **mas não tem canal de volta para
  o modelo** e não identifica o autor. Entra como sensor (§3.8), não como peça central. Detalhe em
  [`medicao-hooks.md`](medicao-hooks.md).
- **Q-005** Derivar faixa de linha do `old_string` custa uma leitura de arquivo no caminho quente. Medir; se passar de 150 ms, cair para claim de arquivo inteiro com `range: null`.
- **Q-003 (herdada)** `crossSessionInbound` em `accept` global? Com `bypassPermissions` o default é `hold` — mensagem entre sessões pode ficar presa esperando clique.

---

## 8. Verificação do design

Cada AC da spec continua válido. **Muda o AC-005**: era `deny` em arquivo com dono; passa a **`warn`**,
e ganha um AC irmão para o caso `Write`-sobre-arquivo-com-claim. A tabela de rastreabilidade e os
testes `@spec:AC-xxx` entram na fase Tasks, com `onp-spec verify` + `audit --ci` antes de declarar pronto.
