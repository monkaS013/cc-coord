# Como retomar a feature cc-coord

> **Atualização 17/09/2026 — a feature está INSTALADA e em uso desde 12/09.** O corpo deste arquivo
> abaixo é de 11/09 e descreve a fase anterior (código pronto, nada instalado); vale como histórico.
> O estado de hoje: T-001 a T-026 concluídas, 322 testes, `onp-spec verify` 23/23 com prova PASS,
> `audit --ci` sem erro. **Leia `medicao-uso-producao.md` antes de mexer**: os quatro últimos
> defeitos corrigidos vieram do log de uso real, não de teste nem de auditoria — inclusive um gate
> cego em caminho com espaço e 1.140 claims de turno que nunca eram liberados.

Atualizado em 2026-09-11, fim do dia. Abrir a sessão dentro do repo:

```
cd C:/Users/ViniciusMoraisHDT/dev/cc-coord && claude
```

---

## Onde a feature está

**Todo o código está escrito e verificado.** 146 testes, `onp-spec verify` com **17/17 critérios em
prova PASS**, `onp-spec audit --ci` em **0 erros**. Nada foi escrito em `~/.claude/` — a feature está
inteira dentro do repo, inerte, esperando a instalação.

| Fase | Estado |
|---|---|
| Specify (`spec.md`) | pronta, 04/09 |
| Design (`design.md`) | pronto, 11/09 — **vale sobre a spec** em 7 pontos (D-01..D-07) |
| Task 1 — medição de hooks (`medicao-hooks.md`) | feita, 11/09 — **leia antes de tocar em qualquer hook** |
| Tasks (`tasks.md`) | T-001 a T-011 concluídas |
| Perf (`medicao-perf.md`) | medida, 11/09 — **RNF-04 reprovou**, T-017 em curso |

## O que falta, em ordem

1. **T-017 — caber em 150 ms.** A medição reprovou o RNF-04: p95 de 213-227 ms contra o limite de 150.
   Causa medida: **custo fixo de import**, não algoritmo — subir o Python e importar 4 módulos custa
   94 ms p50 (interpretador vazio: 31 ms), sobrando ~56 ms para a lógica. Saída em implementação: fast
   path que decide o caso comum lendo dois diretórios, com import tardio. **O limite de 150 ms não se
   afrouxa** — é requisito do Vinicius; se não couber, a decisão é dele.
2. **T-016 — citar o commit alheio.** `policy.py` lê `contexto["commits_alheios"]` e nenhum entrypoint
   preenche. A recusa de `git commit` funciona (por peer viva no repo), mas sem citar o commit, que é
   metade do AC-007.
3. **⛔ T-012 — instalador. FRONTEIRA DE APROVAÇÃO.** É a primeira escrita em
   `~/.claude/settings.json`, e ela **afeta todas as sessões abertas**, não só a que roda o comando.
   Não executar sem o Vinicius dizer sim, e num momento sem trabalho crítico em voo. Antes: `ListAgents`
   + `SendMessage` avisando as peers.
4. **T-013 — ensaio com duas sessões reais.** Roteiro pronto em `tools/ensaio/roteiro.md`, 8 cenários.
5. **T-014 — fechamento**: `verify` + `audit --ci` de novo, com 0 erro.

## O que NÃO redescobrir (custou medição)

- **`FileChanged` é sensor, não canal.** Existe, dispara em 0,62 s para escrita de outro processo —
  mas o retorno dele **não chega ao modelo** (só `watchPaths` e mensagem de tela são aproveitados) e o
  payload **não diz quem alterou**. Dispara também para a escrita da própria sessão: filtrar o eco.
- **`additionalContext` num hook de `Stop` prende a sessão em laço** (medido: 10 disparos). Hook de fim
  de turno sai calado.
- **`SessionEnd` rejeita `hookSpecificOutput`.**
- **`systemMessage` vai para a tela do usuário, nunca para o modelo.**
- **O `deny` sai com exit 0.** No `PreToolUse`, "other exit codes — continue with tool call": exit 1
  faria a ação **passar**, com a recusa aparecendo na tela. Quem bloqueia é o JSON.
- **`NotebookEdit` usa `notebook_path`**, não `file_path`.
- **Identidade de dono = `(session_id, agent_id)`** — dentro de subagente o `session_id` é o do pai.
- **O modelo é testemunha não confiável do que recebeu.** Verificação de injeção se faz por `grep` no
  transcript JSONL, não perguntando à sessão.
- **Prova PASS não prova caminho ponta a ponta** (foi o caso do AC-007). A rastreabilidade liga
  critério → teste; quem liga critério → realidade é o ensaio da T-013.

## Convenções do repo

- Python 3.14 stdlib apenas (`C:/Python314/python.exe`). Sem pytest — `unittest` emitindo TAP via
  `tests/run_tap.py`, que é o que dá granularidade por AC ao `onp-spec`.
  ⚠️ Esse runner já teve um bug que fazia **exit 0 com teste falhando**; se mexer nele, prove com um
  mutante (teste que falha de propósito) antes de confiar.
- **Duas camadas de spec, de propósito:** `.specs/<feature>/` guarda a narrativa (problema, medições,
  evidência) e `.spec/features/<feature>/` guarda a prova no formato que o `onp-spec` parseia
  (`#### AC-xxx` + Dado/Quando/Então). Os formatos não casam; cada AC aparece nas duas.
- Estado em `CCOORD_HOME` (default `~/.claude/coord`). **Em desenvolvimento e teste, sempre tempfile** —
  nada é criado em `~/.claude/` antes da T-012.
- Sessões: `CCOORD_SESSIONS_DIR` (default `~/.claude/sessions`), **somente leitura**.

## Regras operacionais desta feature

- `ListAgents` **antes da primeira edição**; `SendMessage` às peers ao tocar em `~/.claude/settings.json`,
  `hooks/`, `rules/`, na pasta de memória ou no vault — são recursos da máquina, e **nenhum `git status`
  os cobre**.
- No `MEMORY.md` e na pasta de memória: **Edit pontual com âncora curta, nunca reescrita.** Dois Edits
  ancorados coexistem; um `Write` apaga o do outro em silêncio.
- Ao avisar a peer de um registro, dizer **arquivo e âncora** — evitar colisão não evita duplicação.
- **Autoria de recurso se mede, não se deduz** (`CreationDate` + linha de comando). Servidor, browser e
  lock sobrevivem à sessão que os criou.
- Kill de processo: `deny` + perguntar ao Vinicius. Autorização dele vale para aquela limpeza e **não se
  pede a uma peer**.
