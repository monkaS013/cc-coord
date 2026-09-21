# Como retomar a feature cc-coord

> **Atualização 21/09/2026 (fim do dia) — o furto de alvo está FECHADO. A entrada abaixo, que o
> declarava aberto, está SUPERADA: leia esta primeiro.**
> Conserto em `13a1477`, publicado e verificado contra o servidor. **`pytest -q` devolve
> `1 failed, 385 passed, 231 subtests` e rc=1** — a falha é o gate de p95 (RNF-04), que reprova por
> carga da máquina e **não executa nenhuma** das funções alteradas (provado por contagem de chamadas
> em runtime, com controle positivo). O "rc=0" que esta entrada afirmava antes era o rc COM aquele
> teste deselecionado: número certo, recorte omitido. `onp-spec verify` 28/28 e `audit --ci` limpo
> (1 aviso pré-existente, glob `src/**/*.js` sem match), ambos com exit 0, com AC-028 e T-039.
>
> **O eixo do critério, que custou quatro versões:** não é valor contra posição, é seleção PRÓPRIA do
> segmento do verbo contra seleção HERDADA de outro segmento. Repetir a seleção em cada verbo é retry
> legítimo; reaproveitar a do vizinho é carona. Quatro versões caíram antes, todas com a suíte verde,
> e cada uma virou teste: `continue` no laço promovia o decoy mais distante; recusa por valor sem a
> tentativa nova recusava retry; recusa por posição reabria o furto com ~12 caracteres plantados; e a
> tentativa nova, sem o teto de distância que a irmã sempre teve, deixava decoy distante mascarar o
> fail-closed. **Cinco rodadas de auditoria adversarial: as quatro primeiras acharam um defeito cada,
> a quinta não achou nenhum em 16 comandos.**
>
> **Diferencial sobre 29.582 comandos reais:** 3 divergências (0,01%), ZERO perda de id, 0 exceções.
> Foi esse número que derrubou duas das quatro versões — a suíte aprovava as duas.
>
> **O QUE CONTINUA ABERTO, e é pré-existente (não foi introduzido nem fechado por este trabalho):**
> 1. **Duas âncoras no MESMO segmento**, ligadas por pipe: a segunda resolve pela seleção própria e
>    nunca passa pela recusa por valor. Fechar isso é extensão de feature — a tentativa 3 teria de
>    receber e checar `alvos_consumidos` também.
> 2. **Caminho NÃO VERBAL em sequência** (`.Kill()`, `wmic ... terminate`, `Invoke-CimMethod`): o
>    segundo kill se perde. Esse ramo nunca passa pelo laço por âncora.
> 3. **Heredoc tratado como comando, não como dado** (débito declarado, custo medido em 0,02%):
>    mensagem de commit ou documentação que cite um kill com alvo explícito emite o sentinela.
> 4. **Gate de p95 do RNF-04 reprova nesta máquina sob carga** — e isso é do gate, não do conserto:
>    provado por contagem de chamadas em runtime, o `Edit` que o gate exercita **não executa nenhuma**
>    das funções alteradas (controle positivo: um `Bash` com kill executa as quatro). O HEAD reprova
>    igual. Falta decidir se o gate ganha tolerância a ambiente ou se passa a medir sem subprocesso.
>
> Scripts de verificação e o corpus ficam em `~/dev/cc-coord-verif/` (fora do repo de propósito: o
> corpus tem caminhos de usuário e este repositório é público).

> **[SUPERADA — mantida como registro] Atualização 21/09/2026 — 9ª auditoria: 1 achado ALTA (furto de alvo) e 1 débito declarado.**
> HEAD `bd2e963`, **5 commits locais ainda SEM push**. 380 testes, 0 falhas. O push está segurado por
> decisão do Vinicius enquanto a curva de regressão não fechar — e ela **não fechou**.
>
> **1. ACHADO ALTA, ABERTO — "furto de alvo" (target theft). PRÉ-EXISTENTE, não é regressão.**
> Um kill com alvo explícito ANTES de um kill amplo sem alvo próprio faz o segundo herdar o id do
> primeiro, e o comando inteiro sai `allow`. Reproduzido nas DUAS versões (idêntico antes e depois de
> `bd2e963` — o commit não introduziu e não fechou):
>
> - Comando: `taskkill /F /IM notepad.exe; Get-Process | Where-Object { $_.Id -gt 0 } | ForEach-Object { Stop-Process -Id $_.Id }`
> - `classify()` devolve só `['process:notepad.exe']`; `decide()` sem claim → **allow**. O segundo kill,
>   que mata todo processo com `Id > 0`, não tem recurso nenhum representando-o.
> - **Controle que discrimina:** o mesmo trecho amplo SOZINHO devolve `process:alvo-nao-identificado`
>   → **deny**. Dois alvos explícitos distintos aparecem os dois (não há furto). O idioma legítimo do
>   CIM (`$p = Get-CimInstance ...; $p | ForEach-Object { Stop-Process ... }`) continua allow — ou seja,
>   o mecanismo que causa o furto é o MESMO que faz o caso legítimo funcionar.
> - **Mecanismo:** em `classify.py`, `_alvo_a_esquerda_do_verbo` busca numa janela de 600 chars que
>   atravessa `;`/`&&`/`||` de propósito. Quando o segmento do 2º verbo não tem candidato local, a
>   janela alcança o alvo do 1º verbo, JÁ consumido. Como o alvo volta não-`None`, o ramo do sentinela
>   (`elif _ALVO_INDETERMINADO not in alvos`) nunca roda para aquela posição.
> - **Conserto proposto (não implementado):** não deixar a janela-à-esquerda reaproveitar alvo já
>   atribuído a uma âncora anterior do mesmo comando, quando há mais de uma âncora. Cuidado: mexer
>   nisso é mexer no mesmo mecanismo do caso legítimo do CIM — precisa dos dois controles acima como
>   teste antes de qualquer edição.
> - **Consequência para a mensagem do `bd2e963`:** ela afirma "o que não dá para identificar vira
>   recusa, nunca omissão". A omissão sobrevive por outra porta. Publicar assim repete o erro que o
>   `cdeebd1` já corrigiu uma vez ("o commit anterior alegava proteção que não existia").
>
> **2. DÉBITO DECLARADO — heredoc é tratado como comando, não como dado.** Mensagem de commit ou
> documentação que CITE um kill com alvo explícito emite o sentinela e cai em aviso; o próprio
> `bd2e963` precisou ser escrito por arquivo. **Custo medido, não estimado:** diferencial das duas
> versões sobre **29.582 comandos únicos** dos JSONL de `~/.claude/projects/**` deu **6 divergências
> (0,02%), 0 perda de cobertura, 0 exceções** — e as 6 são citação de kill em texto (5 delas do próprio
> trabalho de auditoria), todas já com outro recurso de kill no mesmo comando. Ou seja, mudança de
> GRAU, não allow→deny. Decisão: fica como débito, não conserto — o custo não paga mexer no
> `classify.py` de novo. Tratar heredoc como literal é o conserto natural quando for a hora.
>
> **3. O que a 9ª auditoria confirmou LIMPO:** o teste novo `test_verbo_sem_alvo_emite_sentinela_...`
> não é vacuidade — falha de fato contra o código velho nos dois casos. E o `bd2e963` não causou
> nenhuma regressão de cobertura no corpus real.

> **Atualização 18/09/2026 — pendência 1 ENTREGUE, em produção, e o ciclo de auditoria ENCERRADO.**
> HEAD `345a141`, sincronizado com o remoto. 366 testes, `onp-spec verify` 27/27 com prova PASS,
> `audit --ci` limpo, 9/9 entrypoints instalados batendo por sha256, 15 hooks de terceiros intactos.
>
> **Quatro auditorias adversariais**, e o veredito da última foi explícito: pode fechar, 0 ALTA,
> nenhum risco de runtime em pé. O perfil converge e é o que justifica parar — rodada 1 (código) 5
> achados/0 ALTA · rodada 2 (testes) **3 ALTA, todos sobre PROVA** · rodada 3 (o conserto da 2) 0
> ALTA/2 MÉDIA · rodada 4 0 ALTA/1 MÉDIA já obsoleta. O achado migrou de "o código está errado" para
> "a prova é fraca" para "o registro está impreciso".
>
> **O QUE FALTA, em ordem:**
> 1. **T-034 — a prova em USO REAL.** É a única coisa que nenhuma auditoria substitui: precisa de uma
>    janela de uso com o hook rodando. Rodar `scratchpad/mede_fronteira_turno.py` (o script está
>    descrito em `medicao-fronteira-de-turno.md`) e exigir **os DOIS lados**: a fração de ciclos que
>    atravessam ≥1 prompt cai de 20,1% para ~0 **E** as disputas de "mesmo turno" (40 em 5 dias, que
>    são a feature funcionando) NÃO podem virar 0 — se virarem, o release está comendo turno vivo.
> 2. Dívidas declaradas, medidas e sem dano observável: asserts de contagem tautológicos no
>    `test_install` (entrada errada no `HOOKS_SPECS` passa), `agent_id=""` em claim JÁ GRAVADO em
>    disco, `events.log` sem rotação, `sort_keys` no `--json`. E a pendência 3 original (atomicidade
>    de `_renovar`, assimetria main×subagente), que tem spec própria.
> 3. O delta dos commits `ef31698` e `345a141` não teve olho independente — decisão do Vinicius de
>    parar, com o custo e o valor esperado na mesa.
>
> **Três coisas que custaram medição e não devem ser redescobertas:** este build **não envia** o campo
> `source` no `UserPromptSubmit` (exigir `== "user"` deixaria o hook inerte); mensagem de peer vira
> turno **próprio**, então liberar ali está certo; e o evento **não** dispara dentro de subagente
> (medido com `SubagentStart` como controle negativo). Tudo em `medicao-hooks.md` §2-bis e §5-bis.
>
> **Atualização anterior (17/09, 16h09) — pendência 1 INSTALADA e em produção.** São **9 hooks** agora.
> Verificado: `settings.json` com o hook novo ao lado do `context_alert.py` de terceiro (24 hooks, 15
> de terceiros intactos), sha256 de 9/9 entrypoints batendo, backup `settings.json.bak-20260917-160928`,
> e o hook instalado exercitado (exit 0, stdout vazio, claim liberado).
> **Única ponta aberta: T-034** — a prova em uso REAL. Repetir `mede_fronteira_turno` sobre uma janela
> nova: a fração de ciclos que atravessam ≥1 prompt tem de cair de 20,1% para ~0 **e** as disputas de
> "mesmo turno" (40 em 5 dias, que são a feature funcionando) não podem virar 0 — se virarem, o release
> está comendo turno vivo. Os dois lados, senão a medida não vale.
>
> **Atualização anterior (17/09, noite) — pendência 1 implementada e verificada, antes de instalar.**
> 348 testes verdes, `onp-spec verify` 27/27 com prova PASS, `audit --ci` limpo. T-030 a T-032, T-035
> e T-036 concluídas. **O que falta é a T-033: a instalação, que escreve no `settings.json` global e
> afeta todas as sessões abertas — fronteira de aprovação, igual à T-012.** O hook novo
> (`hooks/coord_user_prompt.py`) está no `HOOKS_SPECS`, então `ccoord install` já o inclui: são 9
> hooks agora, não 8.
> Também consertado: **T-035** (o JSON do hook saía em cp1252 — todo aviso acentuado chegava ilegível
> à peer) e **T-036** (gate RNF-04 com N=60 em vez de 20, sem tocar no limite de 150 ms).
>
> **Atualização anterior (17/09, fim da tarde) — pendência 1 especificada, nada implementado.**
> Ler nesta ordem: `medicao-fronteira-de-turno.md` (o tamanho real do problema),
> `spec-release-no-inicio-do-turno.md` (a spec) e `medicao-hooks.md §5-bis` (quatro fatos sobre o
> `UserPromptSubmit` lidos no binário). Tasks T-030 a T-034 em `.spec/.../tasks.md`, todas pendentes.
> **A próxima é a T-030, que é uma MEDIÇÃO, não código** — a leitura do binário já refutou a ASM-008 e
> metade da ASM-007, e escrever o hook antes de medir o resto é a quarta tentativa errada.
> `onp-spec audit --ci`: 4 erros, todos `AC_SEM_TESTE` dos ACs novos (24, 25, 26, 27) — é o gate
> cobrando o que ainda não foi feito, não regressão. Suíte: 335 testes; o único vermelho é o gate de
> perf RNF-04 com n=20, que passa 3/3 isolado e reprova sob carga (piso do interpretador em 54,6 ms
> contra os 31 ms normais).
>
> 🔴 **Achado fora da pendência 1, e mais urgente que ela: T-035.** O stdout de hook em pipe sai em
> cp1252 nesta máquina e `hookio._imprimir` usa `ensure_ascii=False` — todo aviso com acento (que são
> todos) sai em bytes inválidos em UTF-8. Medido aqui em runtime, depois do aviso da peer
> `home-piped-liskov`, que viu o texto chegar corrompido ao modelo no hook dela. Conserto é uma linha;
> o que falta é medir se o harness corrompe o texto ou perde o JSON inteiro (aí o `deny` fica mudo).

> **Atualização 17/09/2026 — a feature está INSTALADA e em uso desde 12/09.** O corpo deste arquivo
> abaixo é de 11/09 e descreve a fase anterior (código pronto, nada instalado); vale como histórico.
> O estado de hoje: T-001 a T-026 concluídas, 322 testes, `onp-spec verify` 23/23 com prova PASS,
> `audit --ci` sem erro. **Leia `medicao-uso-producao.md` antes de mexer**: os quatro últimos
> defeitos corrigidos vieram do log de uso real, não de teste nem de auditoria — inclusive um gate
> cego em caminho com espaço e 1.140 claims de turno que nunca eram liberados.

Atualizado em 2026-09-11, fim do dia. Abrir a sessão dentro do repo:

```
cd C:/Users/usuario/dev/cc-coord && claude
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
