# Coordenação entre sessões simultâneas — Tasks

**Spec:** `spec.md` (04/09) · **Design:** `design.md` (11/09, vale sobre a spec) · **Medição:** `medicao-hooks.md` (11/09)
**Fase:** Tasks (tlc-spec-driven) · **Data:** 2026-09-11
**Escopo:** Large — 15 tasks, 4 ondas, com um ponto de aprovação explícito antes de tocar em `~/.claude/`.

---

## 0. Alterações de critério vindas do Design e da Task 1

| AC | Estado | Motivo |
|---|---|---|
| **AC-005** | **ALTERADO**: era `deny` em arquivo com dono → agora **`warn`** | D-03: avisar sempre, bloquear só o irreversível (decisão do Vinicius, 11/09) |
| **AC-017** | **NOVO**: `Write` (arquivo inteiro) sobre arquivo com claim de peer viva → `warn` **forte**, sugerindo `Edit` cirúrgico | D-02: reescrita apaga o que entrou na janela |
| **AC-013** | **NOVO**: o hook de `Stop` não emite `hookSpecificOutput`; dois turnos seguidos não disparam `Stop` em cadeia | Medido: `additionalContext` em `Stop` gerou **10 disparos em loop** |
| **AC-014** | **NOVO**: o hook de `SessionEnd` não emite `hookSpecificOutput` | Medido: `SessionEnd` **rejeita** o wrapper na validação |
| **AC-015** | **NOVO**: `FileChanged` cuja alteração é da própria sessão não gera aviso (filtro de eco) | Medido: o watcher dispara igual para escrita própria |
| **AC-016** | **NOVO**: aviso de arquivo alterado por peer aparece no **próximo `PreToolUse`/`PostToolBatch`**, não no `FileChanged` | Medido: retorno do `FileChanged` não chega ao modelo |

---

## 1. Ondas e dependências

```
ONDA 0  T-01 medição ────────────────────────────── FEITA
ONDA 1  T-02 esqueleto + onp-spec init
           ├── T-03 sessions.py      [P]
           ├── T-04 claims.py        [P]
           └── T-05 classify.py      [P]
ONDA 2  T-06 policy.py  (dep: 03,04,05)
        T-07 hookio.py  (dep: 06)
ONDA 3  T-08 cli.py         (dep: 03,04)   [P]
        T-09 entrypoints    (dep: 07)      [P]
        T-10 regra escrita  (dep: 06)      [P]
        T-11 perf <150 ms   (dep: 07)
ONDA 4  ⛔ APROVAÇÃO DO VINICIUS
        T-12 instalador (mexe em ~/.claude/settings.json)
        T-13 ensaio ponta a ponta com 2 sessões reais
        T-14 onp-spec verify + audit --ci
        T-15 registro no vault + memória
```

`[P]` = paralelizável (subagente por task).

---

## T-01 — Medir os payloads reais de hook ✅ FEITA

- **O quê:** capturar payload literal de `SessionStart`, `PreToolUse`, `Stop`, `SessionEnd` e decidir a Q-004 (`FileChanged`).
- **Onde:** `tools/probe/` (`capture.py`, `probe-settings.json`, `run_probe{,2,3,4}.sh`).
- **Pronto quando:** payloads capturados de sessão real + Q-004 respondida com evidência.
- **Resultado:** `medicao-hooks.md`. `FileChanged` existe, dispara em 0,62 s para escrita externa, **não fala com o modelo** → vira sensor, não peça central. O design **não caiu**; ganhou §3.8 e 3 regras novas.

---

## T-02 — Esqueleto do repo e enforcement mecânico

- **O quê:** `git init`; `src/ccoord/` + `tests/`; `onp-spec init` com `specDir` apontando para `.specs/coordenacao-multissessao` e `testCommand` = `C:/Python314/python.exe -m unittest discover -s tests`.
- **Por que unittest e não pytest:** RNF-01 — stdlib apenas; pytest seria dependência externa num repo cujo produto roda dentro de hook.
- **Depende de:** —
- **Pronto quando:** `onp-spec audit --ci` roda (pode acusar órfãos, é esperado nesta fase) e `python -m unittest discover` roda com 0 testes.
- **Gate:** `onpspec.config.json` existe e o parser casa com o formato dos ACs da spec. Se não casar, `.spec/` separado (previsto na regra global).

## T-03 — `sessions.py`: descobrir peers vivas  `[P]`

- **O quê:** `me()`, `peers(exclude_pid)`, `is_alive()`. Fonte única: `~/.claude/sessions/<pid>.json` (**somente leitura**). Identidade da própria sessão pelo env `CLAUDE_PID`/`CLAUDE_CODE_SESSION_ID` (medido na T-01), com o arquivo como fallback.
- **Reusa:** registro do harness (D-01) — nada de registro caseiro.
- **Guardas obrigatórias:** leitura defensiva por arquivo (`try/except` individual); `status` é rótulo, não decisão — vocabulário tem ao menos `busy|idle|waiting` e `updatedAt` pode estar 262 s atrasado; liveness forte = PID + `procStart`; ignorar `pidDomain` diferente.
- **Pronto quando:** com um diretório de fixtures, retorna só as vivas e não estoura em arquivo truncado.
- **Testes:** `@spec:AC-012` (parte do mapa), `@spec:AC-006` (nada aqui), fixtures com: arquivo truncado, `status` velho, `pidDomain` de outro host, PID reusado (mesmo pid, `procStart` diferente).

## T-04 — `claims.py`: claim advisory com TTL  `[P]`

- **O quê:** `claim()`, `owner_of()`, `overlapping(path, lines)`, `release(scope)`, `sweep()`, `events.log`.
- **Primitiva:** `os.open(O_CREAT|O_EXCL|O_WRONLY)`. **Proibido tmp+rename** (RNF-06 — `EPERM` no Windows com handle aberto).
- **Unidade:** `(arquivo, faixa de linha)`; `range: null` = arquivo inteiro (D-02).
- **TTL:** arquivo 15 min (`turn`), recurso 60 min (`session`), jitter 10-20% na renovação.
- **Pronto quando:** dois processos concorrentes → exatamente um adquire; claim de dono morto é substituída e o roubo vai para `events.log`.
- **Testes:** `@spec:AC-001` (corrida real com `multiprocessing`, não simulada), `@spec:AC-002`, `@spec:AC-008`.

## T-05 — `classify.py`: `(tool_name, tool_input, cwd) -> [Resource]`  `[P]`

- **O quê:** função pura, sem I/O.
- **Regras que não podem errar:** chavear em **`tool_input.file_path`**, nunca em `cwd` (issue #76727: 29% das escritas negadas em silêncio); `Edit` → faixa derivada do `old_string`; `Write` → arquivo inteiro; `Bash` → kill/`Stop-Process`, `git commit|push|reset|checkout`, bind de porta, `prisma migrate`.
- **Pronto quando:** tabela de casos passa, incluindo `cwd` no checkout primário com `file_path` em worktree.
- **Testes:** `@spec:AC-004`, `@spec:AC-003` (reconhecimento do kill), `@spec:AC-006` (bind de porta), `@spec:AC-007` (git write).

## T-06 — `policy.py`: a tabela de decisão

- **O quê:** `(resource, owner, me, peers) -> allow | warn | deny(razão prescritiva)`. Pura.
- **Política (D-03):** avisar sempre; `deny` **só** em kill de processo e `git commit`/`push` com peer viva. `Edit`/`Write` em arquivo com dono = `warn`.
- **Regra do kill:** nunca decidir por idade do processo — `deny` e mandar perguntar ao Vinicius. Autorização de kill é dele, não se pede a peer.
- **Fail-open/fail-closed:** registro ilegível → `allow` + log; se a ação for kill → `deny`.
- **Depende de:** T-03, T-04, T-05.
- **Testes:** `@spec:AC-003`, `@spec:AC-005` (**warn**, não deny), `@spec:AC-017`, `@spec:AC-006`, `@spec:AC-007`, `@spec:AC-010`.

## T-07 — `hookio.py`: falar com o harness sem quebrar o turno

- **O quê:** ler stdin JSON, montar saída, **nunca** derrubar o turno (exit 0 sempre, exceto o deny explícito).
- **Formatos confirmados na T-01:** `deny` = `hookSpecificOutput{hookEventName, permissionDecision, permissionDecisionReason}` (≤2000 chars); aviso = `additionalContext` (≤8000) em `PreToolUse`/`PostToolBatch`/`SessionStart`; `systemMessage` **não** chega ao modelo.
- **Identidade:** `(session_id, agent_id)` — `session_id` é do pai dentro de subagente (medido).
- **Depende de:** T-06.
- **Testes:** `@spec:AC-011` (sem o wrapper é no-op silencioso), `@spec:AC-009`, `@spec:AC-013`, `@spec:AC-014`.

## T-08 — `cli.py`: `ccoord status | who | release --mine | sweep`  `[P]`

- **O quê:** visibilidade sob demanda (RF-08). Não mexe no `statusline.py` do monitor de uso (fora de escopo).
- **Depende de:** T-03, T-04.
- **Testes:** `@spec:AC-012`.

## T-09 — Entrypoints de hook (no repo, ainda não instalados)  `[P]`

- **O quê:** `hooks/coord_session_start.py`, `coord_pre_write.py`, `coord_pre_bash.py`, `coord_post_batch.py`, `coord_file_changed.py`, `coord_stop.py`, `coord_session_end.py`. Finos: só importam `ccoord`.
- **Regras medidas:** `coord_stop.py` sai **calado** (AC-013) e checa `stop_hook_active`; `coord_session_end.py` não emite wrapper (AC-014); `coord_file_changed.py` só carimba em `coord/changed/` e **filtra o eco da própria sessão** (AC-015), com o aviso saindo no próximo `PreToolUse`/`PostToolBatch` (AC-016).
- **Depende de:** T-07.
- **Testes:** `@spec:AC-015`, `@spec:AC-016`.
- **⚠️ Dívida herdada da T-006:** `policy.py` é pura, então não consegue testar socket; quando o
  chamador não passa `contexto["porta_livre"]`, ela sugere `porta+1` como fallback. **Porta+1 não é
  porta livre — é um palpite**, e um aviso que sugere porta ocupada mente para quem confia nele
  (AC-006 pede porta livre *concreta*). O `coord_pre_bash.py` **tem de medir** uma porta realmente
  livre (`socket.bind(("127.0.0.1", 0))` ou varredura a partir da ocupada) e passá-la no contexto.
  Teste que prova: com a porta seguinte também ocupada, o aviso não pode citá-la.

## T-10 — `rules/coordenacao-sessoes.md` (a política escrita)  `[P]`

- **O quê:** a política de conversa (RF-07): quando desviar, quando esperar com `notify_when_idle`, quando negociar por `SendMessage`, quando escalar ao Vinicius. Inclui as três lições caras: **lock preso ≠ peer trabalhando**, **coordenador morto ≠ recurso órfão**, **dica de peer é pista, não endereço**.
- **Onde:** escrito no repo. A cópia para `~/.claude/rules/` é T-12.
- **Depende de:** T-06.

## T-11 — Custo do caminho quente < 150 ms (RNF-04)

- **O quê:** medir `coord_pre_write.py` e `coord_pre_bash.py` com 3, 10 e 30 claims em disco, e o watcher com 10 e 100 paths.
- **Q-005:** derivar faixa de linha do `old_string` custa uma leitura de arquivo. Se passar de 150 ms, cai para `range: null`.
- **Depende de:** T-07.
- **Gate:** p95 < 150 ms medido, não estimado.

## ⛔ APROVAÇÃO DO VINICIUS — fronteira de `~/.claude/`

Nada abaixo roda sem ele dizer sim, e num momento sem trabalho crítico em voo nas outras sessões:
o `settings.json` global afeta **todas** as sessões, inclusive as que já estão abertas.
Antes de T-12: `ListAgents` + `SendMessage` avisando as peers (regra operacional desta feature).

## T-12 — Instalador (`ccoord install`)

- **O quê:** backup datado do `settings.json`, merge dos 7 hooks (sem remover os existentes: `verify_gate`, `context_alert`, `memory_recall_start`, `obsidian_stop`, `pre_push_migration_gate`, `block_env_edit`), cópia da regra para `~/.claude/rules/`, `@` no CLAUDE.md. `ccoord uninstall` reverte.
- **Depende de:** T-09, T-10, aprovação.
- **Gate:** `claude --debug` numa sessão nova mostra os 7 hooks registrados **e** os hooks antigos ainda rodando.

## T-13 — Ensaio ponta a ponta com duas sessões reais (spec §8)

- **O quê:** A adquire `browser:profile-b`; B tenta matar o perfil e é recusada com razão prescritiva; B manda `SendMessage` + `notify_when_idle`; A encerra; B recebe o aviso e adquire. Nada morto, nada refeito.
- **Verificação:** **grepar o transcript JSONL** das duas sessões pelos marcadores — o modelo é testemunha não confiável do que recebeu (medido na T-01).
- **Depende de:** T-12.

## T-14 — Fechamento mecânico

- `onp-spec verify coordenacao-multissessao` + `onp-spec audit --ci`. **Não declarar pronto sem audit verde (0 ERRO).**
- Só PASS conta como prova; skip é `AC-sem-prova`.

## T-15 — Registro

- `Projetos/` e `Daily/` no vault; memória `project_coordenacao_multissessao` atualizada (Edit pontual no `MEMORY.md`, nunca reescrita).

---

## 2. Rastreabilidade requisito → task → teste

| Req | Tasks | AC coberto |
|---|---|---|
| RF-01 registro de recursos | T-04 | AC-001, AC-002 |
| RF-02 dono morto libera sozinho | T-03, T-04 | AC-002 |
| RF-03 impedir 4 classes | T-05, T-06 | AC-003, AC-005/005a, AC-006, AC-007 |
| RF-04 recusa prescritiva | T-06, T-07 | AC-003, AC-011 |
| RF-05 liberar por turno/sessão | T-09 | AC-008, AC-013, AC-014 |
| RF-06 mapa no início da sessão | T-03, T-09 | AC-012 |
| RF-07 política de conversa | T-10 | — (documental; ensaio em T-13) |
| RF-08 mapa sob demanda | T-08 | AC-012 |
| RNF-01 stdlib | T-02 | gate do `testCommand` |
| RNF-02/03 fail-open / fail-closed | T-06, T-07 | AC-010 |
| RNF-04 < 150 ms | T-11 | gate de perf |
| RNF-06 sem tmp+rename | T-04 | AC-001 |
| Sensor de alteração externa | T-09 | AC-015, AC-016 |

**Sem órfãos:** todo AC tem task; toda task acima de T-02 tem AC ou gate próprio. T-10 é o único entregável sem teste automatizado — coberto pelo ensaio T-13, e isso está declarado, não escondido.
