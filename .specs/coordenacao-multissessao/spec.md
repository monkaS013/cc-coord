# Spec — Coordenação entre sessões simultâneas do Claude Code

- **Feature:** `coordenacao-multissessao`
- **Data:** 2026-09-04
- **Fase:** Specify (tlc-spec-driven)
- **Autor:** Claude, a pedido do Vinicius
- **Estado do gate:** aguarda update do CLI para 2.1.260 antes da fase Execute

---

## 1. Problema

O Vinicius roda várias sessões do Claude Code ao mesmo tempo na mesma máquina, e elas se atropelam.
Não é hipótese: está catalogado em `feedback_sessoes_paralelas_repo` e foi medido nesta sessão.

**Medições desta sessão (04/09/2026, 16:00):**

| O que foi medido | Resultado | Como |
|---|---|---|
| Sessões CLI vivas | 2 (PIDs 78300, 96040) | `~/.claude/sessions/<pid>.json` |
| Transcripts escritos na última hora | 4 sessões distintas | mtime em `~/.claude/projects/<slug>/` |
| Processos `@playwright/mcp` | ~4 **por sessão** | `Get-CimInstance Win32_Process` |
| Versão do CLI | 2.1.216 | `package.json` do npm global |

A medição dos processos corrige um entendimento antigo: **o servidor MCP do Playwright é por sessão**.
O `Browser is already in use` não é disputa de processo, é disputa do `--user-data-dir` em disco. Logo,
contar processos não diz quem está usando o browser, e matar processos do perfil derruba quem está
navegando. Isso já causou dano real duas vezes (formulários de candidatura perdidos, 24/08 e 25/08).

**Danos históricos por tipo de colisão:**

1. **Browser** — perfil travado; a outra sessão relança o Chrome e o preenchimento parcial morre (`about:blank`). Eu reincidi 3× em matar o perfil (25/08, 31/08) apesar da memória proibir.
2. **Git** — commit arrastando WIP alheio; `main` virando `[ahead 1]` com commit de outra sessão no meio do turno; arquivo com mudança de duas sessões, onde não existe commit seletivo (02/09).
3. **Portas e servidores** — dois projetos Astro na 4321; screenshot do projeto errado; servidor de background morto por outra sessão; KPI validado contra servidor com outro estado de dados (12/08).
4. **Arquivos compartilhados** — mesmo arquivo editado por duas sessões; e o mesmo vale para o vault Obsidian e a pasta de memória, que ficam fora de qualquer repo.

## 2. Objetivo

Que as sessões **conversem** e que cada uma **decida sem gerar retrabalho**. Palavras do Vinicius:

> "você conversa entre elas e vc toma a decisão que não va causar impacto negativo no seu trabalho, desde retrabalho ter q apagar etc"

Critério de sucesso é **trabalho jogado fora = zero**, não "a ação foi bloqueada". Atrito é aceitável
quando evita perda; atrito que só irrita é defeito.

## 3. O que já existe no harness (não construir de novo)

Verificado na doc oficial em 04/09/2026.

| Capacidade | Estado | Fonte |
|---|---|---|
| Mensagem entre sessões (`ListAgents` + `SendMessage`) | **Nativo.** Named pipe por sessão no Windows, nunca passa por servidor da Anthropic | [cross-session-messaging](https://code.claude.com/docs/en/cross-session-messaging) |
| Entrega sem interromper tool em execução | **Nativo.** "The receiving Claude reads the message between tool calls during an active turn"; se idle, inicia turno novo | idem |
| Aviso quando a outra sessão termina | **Nativo.** `notify_when_idle` (input do `SendMessage`), one-shot, expira em 12h, só main conversation, só mesma máquina. Requer 2.1.236+ **nas duas** sessões | idem |
| Registro de sessões vivas | **Nativo.** Um arquivo por sessão em `~/.claude/sessions/`, removido ao sair | [claude-directory](https://code.claude.com/docs/en/claude-directory) |
| Isolamento de arquivos por worktree | **Nativo.** `--worktree <nome>` / tool `EnterWorktree`; 4 checagens de enforcement; `git worktree lock` mantido enquanto o agente roda, com sweep que libera lock de sessão morta | [worktrees](https://code.claude.com/docs/en/worktrees) |
| Controle de entrada de mensagens | **Nativo.** `crossSessionInbound`: `accept` / `hold` / `refuse` | cross-session-messaging |
| **Lock de arquivo** | **NÃO EXISTE.** Arquivo alterado em disco depois do Read "can still be edited when `old_string` matches the current content exactly". Há atomicidade, não exclusão mútua | [tools-reference](https://code.claude.com/docs/en/tools-reference) |
| **Registro de recursos** (browser, porta, servidor, migração) | **NÃO EXISTE** | — |
| **Política de quem cede** | **NÃO EXISTE** | — |

Conclusão de arquitetura: **o canal é nativo; o que falta é o registro de recursos, o gate mecânico e a
política de decisão.** É isso que esta feature constrói. Sem o update do CLI o canal não existe nesta
máquina (2.1.216 < 2.1.234 exigido no Windows nativo), e um broker caseiro nasceria redundante.

## 4. Requisitos

### Funcionais

- **RF-01** Registrar, num único lugar da máquina, qual sessão detém qual recurso, com dono, PID, cwd, horário e propósito.
- **RF-02** Detectar dono morto e liberar o recurso sozinho, sem intervenção.
- **RF-03** Impedir mecanicamente 4 classes de ação quando o recurso está com outra sessão viva: kill de processo/browser, git write com histórico misturado, edição de arquivo já em edição, bind de porta ocupada.
- **RF-04** Toda recusa nomeia o dono e **prescreve o comando ou caminho alternativo** — recusa seca causa contorno ou insistência.
- **RF-05** Liberar tudo que é da sessão ao encerrar, e o que é do turno ao fim do turno.
- **RF-06** Injetar no início da sessão o mapa das outras sessões vivas e dos recursos ocupados.
- **RF-07** Codificar a política de conversa: quando desviar, quando esperar via `notify_when_idle`, quando negociar por `SendMessage`, quando escalar para o Vinicius.
- **RF-08** Expor o mapa sob demanda (`/sessoes`).

### Não funcionais

- **RNF-01** Zero dependência externa: stdlib do Python 3.14 (`C:\Python314`). Hook não é lugar de `pip install`.
- **RNF-02** Hook nunca quebra o turno. Falha interna → exit 0 (fail-open), com uma exceção em RNF-03.
- **RNF-03** Exceção fail-closed: ação de **kill**. Se o registro não puder ser lido, recusar o kill — a alternativa (não matar) é sempre segura, e o dano do kill é irreversível.
- **RNF-04** Custo por hook < 150 ms. O gate roda em toda Bash/Edit/Write.
- **RNF-05** Nenhuma escrita fora de `~/.claude/coord/`. O sistema não altera repo do usuário.
- **RNF-06** Aquisição de lease atômica **sem tmp+rename**. No Windows `rename()` dá `EPERM` quando outro processo tem handle no destino; esse padrão foi a raiz do cluster de corrupção do `~/.claude.json` (12+ issues, corrigido em 2.1.61), em que o lock "falhava aberto". Usar `os.open(..., O_CREAT|O_EXCL)`.

## 5. Arquitetura

```
~/dev/cc-coord/                      código, versionado
  src/ccoord/
    registry.py    leases: acquire / release / renew / owner_of / sweep_dead / list_all
    sessions.py    descoberta de sessões vivas + liveness por PID
    classify.py    (tool_name, tool_input) -> [recursos tocados]
    policy.py      (recurso, dono, eu) -> allow | deny(razão prescritiva) | warn
    hookio.py      leitura do stdin JSON e emissão do hookSpecificOutput
    cli.py         ccoord status | who <recurso> | release --mine | sweep
  tests/
  .specs/coordenacao-multissessao/spec.md

~/.claude/coord/                     estado, não versionado
  leases/<slug>.json                 um arquivo por recurso
  events.log                         append-only: aquisição, recusa, roubo de lease órfã

~/.claude/hooks/coord_*.py           entrypoints finos, importam ccoord
~/.claude/rules/coordenacao-sessoes.md   política (carrega sempre, via CLAUDE.md)
```

### 5.1 Namespace de recursos

| Recurso | Escopo | Exemplo |
|---|---|---|
| `browser:<perfil>` | session | `browser:mcp-chrome-62f495f`, `browser:profile-b` |
| `port:<n>` | session | `port:3100` |
| `server:<repo>:<porta>` | session | `server:dashboard-im:8099` |
| `git:<repo-abs>` | turn | `git:C--dev-app-exemplo` |
| `file:<path-abs-normalizado>` | turn | `file:C--dev-...-logcomex.py`, `file:C--Meu Vault-Daily-2026-09-04.md` |
| `db:<repo>:migrations` | session | `db:workday:migrations` |

`file:` cobre o vault e a pasta de memória sem regra especial: qualquer path absoluto entra.

### 5.2 O gate (`PreToolUse`)

Matcher `Bash|Edit|Write|NotebookEdit`, tipo `command`, `command: "C:/Python314/python.exe"` com `args`
(exec direto — nesta máquina `bash` não está no PATH e o shell de hooks é PowerShell).

Decisão emitida **com o wrapper obrigatório** — sem ele a saída é no-op silencioso:

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse",
 "permissionDecision":"deny",
 "permissionDecisionReason":"browser:profile-b está com a sessão api-worker (PID 96040, ~/dev/x) desde 15:42. Tente o servidor playwright default (perfil próprio, costuma estar logado). Se os dois estiverem presos: SendMessage para api-worker + notify_when_idle. NÃO mate o processo — recência não prova dono e o perfil é compartilhado."}}
```

**Regras de chaveamento que a implementação não pode errar:**

- `Edit|Write|NotebookEdit` → chavear no **`tool_input.file_path`**, nunca no `cwd`. Evidência: na issue #76727 (13.782 chamadas, 20 sessões, Windows), **29% das escritas eram sessões com `cwd` no checkout primário escrevendo corretamente em worktree por path absoluto** — um gate por `cwd` negaria essas 4.010 chamadas silenciosamente.
- `cwd` vem do JSON do hook, **não** de `${CLAUDE_PROJECT_DIR}`: a doc de worktrees diz que a variável "still points at the project root where the session started" e que só o campo `cwd` segue o worktree.
- Identidade: `session_id` do payload é o **da sessão pai** mesmo dentro de subagente. Quando `agent_id` está presente, compor a identidade com ele — senão dois subagentes irmãos aparecem como o mesmo dono.
- Toda consulta git em hook usa `git --no-optional-locks` (elimina contenção de `index.lock`).

### 5.3 Liberação

| Evento | Ação |
|---|---|
| `Stop` | libera leases de escopo `turn` (`file:`, `git:`) |
| `SessionEnd` | libera **todas** as leases da sessão (slot hoje vazio no settings) |
| qualquer `acquire` | faz sweep de leases cujo PID não existe mais, e loga o roubo |

Lease válida = PID vivo **e** `renewed_at` dentro do TTL. TTL = 4× o intervalo de renovação (a
referência prática do `claude-presence` é 5 s de broadcast com sessões aparecendo offline acima de
30 s). Liveness por PID é o sinal forte; TTL é só rede de segurança.

### 5.4 Conversa e política (`~/.claude/rules/coordenacao-sessoes.md`)

| Colisão | Decisão, em ordem |
|---|---|
| Browser ocupado | 1) tentar o outro servidor (`playwright-b` ↔ `playwright`) 2) `SendMessage` + `notify_when_idle` 3) fazer a parte offline por `curl`/`urllib`. **Nunca** matar, nunca `browser_close` (deixa o lock) |
| Porta ocupada | subir em porta livre própria. Para validar **valor** (não layout), servidor próprio ou produção — nunca os números de servidor alheio |
| Arquivo em uso | não editar. Integrar por contrato/API, ou pedir a mudança à sessão dona por `SendMessage`. Se urgente e ela estiver ocupada, escalar ao Vinicius com o custo de cada caminho |
| Git write | lease exclusiva; antes do commit, `git log origin/main..HEAD`; arquivo misturado → levar ao Vinicius as duas saídas (corrigir o alheio × commitar com bug conhecido), que é decisão dele |
| Migração de schema | lease `db:*`; se ocupada, adiar e implementar só o que não toca schema |

### 5.5 Worktree por sessão (decidido: sim)

- Sessão que abre num repo onde já há outra sessão viva → worktree próprio (`--worktree <nome>` no start, ou `EnterWorktree` no meio da sessão).
- `.claude/worktrees/` no `.gitignore`; `.worktreeinclude` levando `.env`/`.env.local` para cada worktree novo.
- `--worktree` exige workspace trust prévio no diretório (rodar `claude` uma vez ali antes).
- **Limite documentado, a registrar na regra:** das 4 checagens de isolamento, "For PowerShell commands, Claude Code applies only the working-directory check." O worktree isola Edit/Write com força; para PowerShell o registro de leases continua sendo a defesa real.

## 6. Suposições (ASM)

- **ASM-001** Código em `~/dev/cc-coord` (git próprio), estado em `~/.claude/coord/`. Separar os dois evita que sweep de retenção do `.claude` leve o código.
- **ASM-002** Python 3.14 global, stdlib apenas.
- **ASM-003** O schema de `~/.claude/sessions/<pid>.json` **não é documentado** (observado: `pid`, `sessionId`, `cwd`, `startedAt`, `version`, `status`, `updatedAt`). Ler defensivamente, com fallback em `claude agents --json`, que é interface suportada. Nunca falhar por chave ausente.
- **ASM-004** Hooks no `settings.json` global: a coordenação é da máquina, não de um repo.
- **ASM-005 — RESOLVIDA em 04/09, antes da implementação.** Correção de uma imprecisão minha: sessão **sem** `--name` **não** fica sem nome — "When you don't set one, Claude Code names the session itself" (nome gerado, tipo `bright-running-fox`). O ganho do `--name` é nome reconhecível e menos ambiguidade entre homônimas, não endereçabilidade. Não existe chave de settings nem env var para nome default (conferido na cli-reference). Resolvido por wrapper no perfil do PowerShell — `$PROFILE.CurrentUserAllHosts`, criado 04/09 — que injeta `--name <slug do diretório>` na invocação nua e espelha o valor de `-w/--worktree` em `--name`. 11 casos testados, 11 PASS, e carregamento validado sem `-ExecutionPolicy Bypass`. Detalhe em `reference_claude_code_hooks_windows`.

## 7. Perguntas abertas (Q)

- **Q-001** ~~Atualizar o CLI?~~ **Resolvida 04/09:** sim, `@latest` (2.1.260). Motivo: a doc lista correções de messaging depois do mínimo 2.1.234 — 2.1.236 (bursts reportados como enviados mas descartados; `notify_when_idle`), 2.1.239, 2.1.247, 2.1.248, 2.1.251.
- **Q-002** O gate deve recusar **todo** kill, ou só quando o alvo casa com recurso registrado / padrão de browser-servidor? Proposta: só nesses casos; kill de processo que a própria sessão criou é sempre liberado. **Não bloqueante** — decidir na fase Design.
- **Q-003** `crossSessionInbound` fica em `accept` global? Com `bypassPermissions` o default é **hold** (dialog de aprovação, expira em 5 min). Se o Vinicius roda sessões em modo bypass, mensagem entre sessões pode ficar presa esperando clique. **Verificar depois do update** e ajustar.

## 8. Critérios de aceite (DoD)

Testes com tag `@spec:AC-xxx` no título. Só PASS conta como prova; skip é `AC-sem-prova`.

- **AC-001** *Dado* um recurso livre, *quando* dois processos tentam adquirir a lease ao mesmo tempo, *então* exatamente um obtém e o outro recebe o dono correto.
- **AC-002** *Dado* uma lease cujo PID não existe mais, *quando* outra sessão adquire o recurso, *então* a lease órfã é substituída e o roubo aparece em `events.log`.
- **AC-003** *Dado* `browser:profile-b` com dono vivo, *quando* vem um Bash que mata processos daquele perfil, *então* a decisão é `deny` e a razão cita o servidor alternativo e proíbe o kill.
- **AC-004** *Dado* `cwd` no checkout primário e um `Write` cujo `file_path` está dentro de um worktree, *então* a decisão é `allow` (o gate não chaveia por `cwd`).
- **AC-005** *Dado* `file:X` com lease de outra sessão viva, *quando* vem `Edit` em X, *então* `deny` nomeando a sessão dona.
- **AC-006** *Dado* `port:8099` com lease, *quando* vem um Bash que faz bind nela, *então* `deny` sugerindo porta livre concreta.
- **AC-007** *Dado* `origin/main..HEAD` com commit que não é desta sessão, *quando* vem `git commit`, *então* `deny` citando o commit alheio.
- **AC-008** *Dado* leases de uma sessão, *quando* o `SessionEnd` roda, *então* nenhuma lease daquela sessão permanece.
- **AC-009** *Dado* um hook disparado dentro de subagente, *então* a identidade do dono combina `session_id` **e** `agent_id` (dois subagentes irmãos não colidem entre si).
- **AC-010** *Dado* `~/.claude/coord/` corrompido ou ausente, *quando* vem um `Edit`, *então* a decisão é `allow` (fail-open) e o erro vai para `events.log`; *mas* quando vem um kill, a decisão é `deny` (fail-closed).
- **AC-011** *Dado* uma decisão de recusa, *então* o JSON emitido tem o wrapper `hookSpecificOutput` com `hookEventName` e `permissionDecision` (sem o wrapper, o harness ignora em silêncio).
- **AC-012** *Dado* o `SessionStart`, *então* o `additionalContext` lista as outras sessões vivas com cwd e as leases ocupadas.

**Alterações posteriores** (Design 11/09 e Task 1 11/09 — detalhe em `tasks.md` §0):
`AC-005` passa de `deny` para **`warn`**; entram `AC-017` (`Write` sobre arquivo com claim → warn
forte), `AC-013` (hook de `Stop` sai calado — `additionalContext` ali gera loop, medido),
`AC-014` (`SessionEnd` não aceita `hookSpecificOutput`), `AC-015` (filtrar eco da escrita própria no
`FileChanged`) e `AC-016` (o aviso sai no próximo `PreToolUse`/`PostToolBatch`).

**Verificação ponta a ponta (manual, com duas sessões reais):** abrir duas sessões nomeadas; a sessão A
adquire `browser:profile-b`; a sessão B tenta matar o perfil e é recusada com a razão prescritiva; B
manda `SendMessage` para A pedindo o browser e assina `notify_when_idle`; A encerra; B recebe o aviso e
adquire a lease. Nada foi morto, nada foi refeito.

## 9. Fora de escopo (v1)

- Statusline: não mexer no `statusline.py` do monitor de uso. `/sessoes` cobre a visibilidade.
- Coordenação entre máquinas (exige Remote Control) e entre WSL × Windows nativo (a doc é explícita: home diferente e tipo de socket diferente, não se alcançam).
- Broker HTTP próprio: o canal nativo cobre.
- Resolver merge de trabalho concorrente. O sistema evita a colisão; integrar continua sendo decisão do Vinicius.
- Agent teams (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`): outro modelo (sessões que eu spawno e supervisiono), não sessões independentes que ele mesmo abre.

## 10. Ordem de execução

1. **Gate (ele):** fechar as sessões e rodar o update para 2.1.260. Sem isso o canal não existe.
2. Confirmar na sessão nova: `/list-agents` responde e `/status` mostra `Peer address`.
3. Design → Tasks → Execute da tlc-spec-driven, em sessão limpa, a partir desta spec.
4. `onp-spec init` no repo, `testCommand` = `python -m pytest -q` (ou `node --test --test-reporter=tap` se virar Node), e `onp-spec verify` + `audit --ci` antes de declarar pronto.
