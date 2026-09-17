# Spec — liberar o claim de turno no início do turno seguinte

Pendência 1 do cc-coord. Escrita em 17/09/2026, depois de a medição
(`medicao-fronteira-de-turno.md`) dimensionar o problema e **antes** de qualquer código — porque esta
linha já foi escrita errada três vezes em 17/09 e as três vezes o erro esteve na premissa, não na
implementação.

## Problema

O claim de turno é liberado no `Stop`, mas só no `Stop` que **não** é reentrada. Nesta máquina o
`Stop` tem quatro hooks de terceiros registrados e três deles bloqueiam de fato, então o turno
continua e o último `Stop` também chega com `stop_hook_active=True`: ninguém libera. O claim
sobrevive, é renovado a cada edição e as faixas acumulam entre turnos.

Consequência: a peer que edita uma faixa que eu terminei no turno passado ouve *"colide, mande
SendMessage AGORA"*. É o espelho exato do defeito da T-026 — alarme falso em vez de silêncio
indevido.

**Custo é ruído, nunca bloqueio.** Isto é `warn`; `browser`/`bind` são `scope="session"` e não saem
por release de turno; `git` decide por peer no repo, sem olhar claim.

### Tamanho real, medido

| | |
|---|---|
| ciclos de claim de turno (5 dias) | 3.368 |
| atravessam ≥ 1 prompt do usuário | 678 (**20,1%**) |
| … atravessando exatamente 1 turno | 89,5% deles |
| disputas contra dono de turno já encerrado | **10 em 5 dias** (~2/dia) |
| maior `ranges` observado | 5 (teto é 32 — **nunca foi atingido**) |

O cenário "após 32 turnos o teto estoura e o claim degrada para arquivo inteiro" é projeção, não
medição. O hotspot é a pasta de memória (`MEMORY.md` em 32 ciclos) e o vault — recursos que várias
sessões tocam e que nenhum `git status` cobre.

## Objetivo

Que um claim de turno **não sobreviva ao prompt seguinte do usuário**, sem tocar em exclusão de quem
está trabalhando agora.

Métrica de aceitação, medível com a mesma ferramenta da medição: a fração de ciclos que atravessam
≥1 prompt do usuário cai de 20,1% para ~0 no regime novo, com as disputas "mesmo turno" (40 em 5
dias, que são a feature funcionando) **inalteradas**.

## Decisões já tomadas — não reabrir sem motivo novo

1. **Reentrada de `Stop` não mexe em claim.** Liberar ali quebra o turno em andamento (tentativa 2);
   encurtar o TTL libera recurso de dono vivo — medido: intervalo entre edições tem mediana de 79,4 s
   e 48,2% passa de 90 s (tentativa 3). As duas já foram medidas piores que o problema.
2. **O único sinal confiável de "o turno anterior acabou" é o prompt seguinte.** Não existe outro
   evento que distinga fim de turno de turno-que-continua.
3. **A regra de rejeição só entra se cegar zero caso real** (herdada da T-027).

## Fora de escopo

| | Por quê |
|---|---|
| Claims de escopo `session` (browser, bind) | Atravessam turnos de propósito (T-022); release de turno não os alcança e não deve alcançar |
| Claims criados dentro de subagente | Decisão do dono em 17/09: poupar. Saem pelo TTL ou pelo `Stop`, como hoje |
| A assimetria `_same_owner_identity` main × subagente | É a pendência 3; 16 das 65 disputas. Spec própria |
| Atomicidade de `_renovar` | Pendência 3. Exige repensar a primitiva de lock |
| Injetar qualquer contexto no prompt | O hook sai **calado**: gasta token do usuário e não tem o que dizer |

---

## História

**Como** sessão que divide a máquina com outras, **quero** que meu claim morra quando meu turno morre,
**para que** a peer só ouça "colide" quando eu estiver de fato escrevendo ali.

### Critérios

- **AC-024** — Claim de turno não sobrevive ao prompt seguinte.
- **AC-025** — O release do início do turno não alcança subagente, outra sessão, nem escopo `session`.
- **AC-026** — O hook nunca bloqueia nem fala.

Texto normativo em `.spec/features/coordenacao-multissessao/spec.md` (formato que o `onp-spec`
parseia). Cada AC aparece nas duas camadas, como manda a convenção do repo.

---

## Desenho

**Um hook novo, `hooks/coord_user_prompt.py`, registrado em `UserPromptSubmit`**, que faz uma coisa
só: libera os claims de turno **do main desta sessão** e sai com exit 0 e stdout vazio.

```
UserPromptSubmit  →  source == "user"?  →  identidade(payload)  →  claims.release(dono, "turn", agente_exato=True)  →  exit 0
                          ↓ não
                       exit 0, sem tocar em nada
```

### O filtro de `source` não é zelo — é o que impede a quarta tentativa errada

Lido no binário (build 2.1.261, registrado em `medicao-hooks.md §5-bis`, **não medido em runtime**):
`UserPromptSubmit` dispara para seis origens, e duas delas não são fim de turno.

- `poll_event` dispara **no enqueue**, dito literalmente na descrição do campo — turno possivelmente
  em andamento;
- `system` cobre "peer/channel messages, task notifications, auto-continuation" — ou seja, uma peer me
  mandando `SendMessage` (o canal que esta própria feature usa) pode gerar um `UserPromptSubmit` no
  meio do meu turno.

Liberar ali é a tentativa 2 outra vez, com outra roupa. O erro é assimétrico e a escolha segue o lado
barato: **não** liberar num wakeup legítimo custa esperar o próximo prompt do usuário (é o status quo
de hoje); liberar dentro de um turno vivo destrói a faixa de quem está escrevendo.

Armadilha do próprio filtro: o campo é opcional e o binário avisa que *"payloads may omit it while the
field rolls out"*. Se este build não enviar `source` no composer interativo, a regra `== "user"` deixa
o hook **inerte** — instalado, rodando, e sem efeito nenhum.

### O que a medição de 17/09 respondeu (T-030, rodada 5 do probe)

**Este build não envia `source`.** Payload capturado em headless `-p`: `session_id`,
`transcript_path`, `cwd`, `prompt_id`, `permission_mode`, `hook_event_name`, `prompt`,
`session_title`. Nem `source` (que deveria ser `"sdk"` ali) nem `agent_id`. Ou seja, a regra
`== "user"` teria entregado um hook instalado e sem efeito — exatamente a armadilha acima.

**E a preocupação com `system` se inverteu, medida no transcript.** Mensagem de peer e notificação de
tarefa **viram turno próprio**: `promptId` novo e `Stop` próprio, em sequência. Quando uma delas
chega, o turno anterior já terminou de verdade — liberar ali está certo, não errado. Medido em 15
turnos de uma sessão real, com 4 mensagens de peer entre eles.

**Regra final, escrita para o futuro sem depender do presente:** o hook recusa apenas quando o campo
`source` **vem** e é `poll_event` (o único caso que o binário documenta como disparo no enqueue). Com
o campo ausente — o estado de hoje — funciona normalmente. Os três casos têm teste: `poll_event` não
libera, `user` libera, campo ausente libera.

Fica aberto, e declarado: não medi o caso do usuário **enfileirar** uma mensagem digitando durante um
turno longo. Se o `UserPromptSubmit` disparar no Enter nesse caso, o release cai em turno vivo. O
risco é baixo (a fila é submetida ao fim do turno), mas é suposição, não medição.

`claims.release()` ganha um parâmetro keyword-only `agente_exato: bool = False`. Hoje
`_same_owner_identity(a, b, casar_agent=True)` devolve `True` por `session_id` quando `b.agent_id` é
nulo — ou seja, o release do main apaga **também** os claims de subagente. No `Stop` isso é
inofensivo (o turno acabou para todos); no `UserPromptSubmit` pode alcançar um subagente de
background ainda vivo, que é silêncio indevido — o modo de falha pior desta feature. Com
`agente_exato=True` o casamento é `c.owner.agent_id == owner.agent_id` (`None == None`), e nada mais.

O default `False` preserva o comportamento do `Stop` e do `SessionEnd` byte a byte.

### Por que não dá para fazer sem hook novo

Qualquer coisa que rode "no início do turno" precisa de um evento que dispare no início do turno.
`SessionStart` roda uma vez por sessão; `PreToolUse` não sabe que o turno virou; o `Stop` não sabe
distinguir reentrada de fim. Consultar o transcript de dentro do caminho quente para contar prompts
custa uma leitura de arquivo grande a cada ferramenta — fora do orçamento do RNF-04.

### O que isso custa

Um processo Python a mais **antes de cada prompt do usuário**. Não é o caminho quente de ferramenta
(RNF-04, 150 ms), é o caminho do prompt, que só o humano espera — e ~100 ms antes de a resposta
começar é imperceptível. Ainda assim o gate fica: **p95 < 150 ms**, medido como os outros hooks, com
`tools/bench_hooks.py`.

---

## Suposições

- **ASM-007 (BLOQUEANTE — medir antes de escrever o hook).** Com `source == "user"`, o
  `UserPromptSubmit` dispara quando o prompt é **submetido ao modelo**, e não quando o usuário aperta
  Enter com um turno ainda em andamento. **Parcialmente refutada já na leitura do binário**: para
  `poll_event` o disparo é no enqueue, e `system` inclui mensagem de peer — daí o filtro de `source`
  no desenho. Falta medir em runtime (a) se este build envia `source` no composer interativo e (b) se
  o `source == "user"` de fato só chega depois do turno anterior morrer. Medir com `tools/probe/`
  (enfileirar mensagem durante um turno longo; mandar `SendMessage` de uma peer no meio do turno) e
  ler a ordem real dos eventos capturados — **não** deduzir do nome do evento nem do binário.
  Reprovou → esta spec morre e a pendência 1 volta a ser dívida, com o motivo medido.
- **ASM-008 — REFUTADA na leitura do binário.** Existe um produtor de `UserPromptSubmit` que resolve o
  alvo como `agentId ?? session.id` e monta o payload com `agent_id` preenchido: **o evento pode
  disparar dentro de subagente**. Como o `session_id` dentro de subagente é o da sessão pai, um release
  que casasse só por `session_id` mataria os claims do main a partir do subagente. É a segunda razão,
  independente da primeira, para `agente_exato=True` — a decisão de poupar subagente cobria isto por
  sorte, não por projeto.
- **ASM-009.** O payload traz `session_id` utilizável. Sem ele, `identidade()` cai para
  `CLAUDE_CODE_SESSION_ID`; sem os dois, o dono sai com `session_id=""` — e aí o hook **não libera
  nada** (AC-026), porque dono vazio casaria com claim de dono vazio de qualquer sessão.

## Perguntas em aberto

- **Q-007.** O `Stop` continua liberando tudo (inclusive subagente) e o `UserPromptSubmit` poupa
  subagente. Essa assimetria é deliberada, mas deixa o claim de subagente sem release quando o `Stop`
  nunca roda limpo. Vale medir depois, no regime novo, quantos claims de subagente ficam para o TTL —
  se for material, vira spec própria junto da pendência 3.
- **Q-008.** O hook novo entra em `settings.json`, que afeta **todas as sessões abertas**. Instalar
  exige o OK do Vinicius e aviso às peers por `SendMessage` — mesma fronteira da T-012.

## Verificação ponta a ponta

1. `C:/Python314/python.exe tests/run_tap.py` verde, com os testes novos marcados `@spec:AC-024/025/026`.
2. `onp-spec verify coordenacao-multissessao` com prova PASS para os três ACs novos, e
   `onp-spec audit --ci` com 0 ERRO.
3. Mutante provado morto nos três (remover a guarda deixa teste vermelho) — a suíte já teve caso de
   teste que passava com a guarda removida (T-023).
4. `tools/bench_hooks.py` com o hook novo: p95 < 150 ms.
5. **Depois de instalado**, repetir `scratchpad/mede_fronteira_turno.py` sobre uma janela nova de uso
   real: a fração de ciclos que atravessam ≥1 prompt tem de cair para ~0 e as 40 disputas de "mesmo
   turno" não podem virar 0 — se virarem, o release está comendo turno vivo.
