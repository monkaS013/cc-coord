# Spec: Coordenacao multissessao

> feature: coordenacao-multissessao
> status: em-implementacao

<!--
  Esta é a CAMADA DE ENFORCEMENT (onp-spec): os critérios de aceite em formato
  verificável, um por teste. A especificação narrativa — problema, medições,
  evidências, decisões de desenho — vive em:
    ../../../.specs/coordenacao-multissessao/spec.md          (Specify, 04/09)
    ../../../.specs/coordenacao-multissessao/design.md        (Design, 11/09 — vale sobre a spec)
    ../../../.specs/coordenacao-multissessao/medicao-hooks.md (Task 1, 11/09 — payloads medidos)
    ../../../.specs/coordenacao-multissessao/tasks.md         (quebra em tasks, 11/09)
  Os dois formatos não casam (o onp-spec exige `#### AC-xxx` + Dado/Quando/Então;
  a narrativa é prosa com evidência). Mantidos separados de propósito, como
  previsto na regra global: aqui mora a prova, lá mora o porquê.
-->

## Contexto

O Vinicius roda várias sessões do Claude Code ao mesmo tempo na mesma máquina e elas se atropelam:
browser travado, commit arrastando trabalho alheio, porta ocupada, mesmo arquivo editado por duas
sessões. O custo que ele quer eliminar é **trabalho jogado fora**, não a ação bloqueada. Esta feature
faz as sessões se enxergarem e decidirem sem gerar retrabalho: avisa sempre, bloqueia só o irreversível.

## Histórias

### US-001 — Não perder trabalho para outra sessão

Como pessoa que roda 3-6 sessões simultâneas, quero que cada sessão saiba o que as outras estão
fazendo, para que nenhuma apague, mate ou refaça o trabalho da outra.

#### AC-001 — Duas sessões não tomam o mesmo recurso

- **Dado** um recurso livre
- **Quando** dois processos tentam adquirir a lease ao mesmo tempo
- **Então** exatamente um obtém o recurso e o outro recebe a identificação do dono correto

#### AC-002 — Recurso de sessão morta volta a ficar livre

- **Dado** uma lease cujo PID não existe mais
- **Quando** outra sessão adquire aquele recurso
- **Então** a lease órfã é substituída e o roubo fica registrado em `events.log`

#### AC-003 — Matar o browser de outra sessão é recusado com alternativa

- **Dado** `browser:profile-b` com dono vivo
- **Quando** chega um comando que mata processos daquele perfil
- **Então** a ação é recusada e a recusa nomeia o dono, indica o servidor alternativo e proíbe o kill

#### AC-004 — Worktree não é confundido com o checkout primário

- **Dado** uma sessão cujo `cwd` é o checkout primário
- **Quando** ela escreve num arquivo cujo caminho absoluto está dentro de um worktree
- **Então** a ação é liberada (a decisão usa o caminho do arquivo, nunca o diretório da sessão)

#### AC-005 — Editar arquivo com dono avisa, não bloqueia

- **Dado** um arquivo com claim de outra sessão viva
- **Quando** chega um `Edit` naquele arquivo
- **Então** a sessão recebe um aviso nomeando a dona e a faixa de linhas dela, e a edição segue permitida

#### AC-017 — Reescrever arquivo com dono recebe aviso forte

- **Dado** um arquivo com claim de outra sessão viva
- **Quando** chega um `Write` (que reescreve o arquivo inteiro)
- **Então** o aviso é mais forte que o de `Edit` e sugere edição cirúrgica no lugar da reescrita

#### AC-018 — Kill que o Vinicius nomeou é aviso, não recusa

- **Dado** um processo com claim de outra sessão viva, e um turno em que o Vinicius **nomeou esse alvo** junto de um pedido de kill
- **Quando** chega o comando de kill
- **Então** a decisão é `warn` — dizendo quem perde trabalho e o que fazer antes de confirmar — e **não** `deny`; e a razão não pede a autorização de quem já deu a ordem
- **E** quando o alvo **não** foi nomeado pelo usuário (kill nascido de inferência minha), a decisão continua sendo `deny`

#### AC-006 — Porta ocupada recebe porta livre concreta

- **Dado** `port:8099` com lease de outra sessão
- **Quando** chega um comando que faz bind naquela porta
- **Então** a sessão é avisada e recebe o número de uma porta livre

#### AC-007 — Commit não sai com trabalho alheio no meio

- **Dado** que `origin/main..HEAD` tem commit que não é desta sessão
- **Quando** chega um `git commit`
- **Então** a ação é recusada citando o commit alheio

#### AC-008 — Fim de sessão devolve tudo

- **Dado** uma sessão com leases adquiridas
- **Quando** a sessão encerra
- **Então** nenhuma lease daquela sessão permanece

#### AC-009 — Dois subagentes irmãos não colidem entre si

- **Dado** um hook disparado de dentro de um subagente
- **Quando** a identidade do dono é montada
- **Então** ela combina o identificador da sessão e o do subagente, e dois irmãos aparecem como donos distintos

#### AC-010 — Estado corrompido não trava o trabalho, mas trava o kill

- **Dado** o diretório de estado ausente ou corrompido
- **Quando** chega uma edição de arquivo
- **Então** a ação é liberada e o erro é registrado; **e quando** chega um kill, a ação é recusada

#### AC-011 — Recusa mal formatada não passa despercebida

- **Dado** uma decisão de recusa
- **Quando** ela é emitida
- **Então** o JSON traz o invólucro com o nome do evento e a decisão de permissão (sem ele o harness ignora em silêncio)

#### AC-012 — A sessão começa sabendo quem mais está trabalhando

- **Dado** o início de uma sessão
- **Quando** o mapa é montado
- **Então** ele lista as outras sessões vivas com seus diretórios e os recursos ocupados

### US-002 — O aviso chega sem quebrar a sessão

Como quem depende dessas sessões o dia inteiro, quero que o mecanismo de aviso nunca prenda nem
derrube um turno, para que a coordenação não custe mais do que a colisão que ela evita.

#### AC-013 — Fim de turno não vira laço

- **Dado** o fim de um turno com liberação de recursos
- **Quando** o hook de encerramento responde
- **Então** ele não devolve contexto ao modelo e a conversa não é reaberta (medido: contexto ali gerou 10 reentradas em laço)

#### AC-014 — Fim de sessão responde no formato aceito

- **Dado** o encerramento da sessão
- **Quando** o hook responde
- **Então** ele não emite o invólucro de saída (o evento rejeita esse formato na validação)

#### AC-015 — O sensor não avisa sobre a própria sessão

- **Dado** que a própria sessão acabou de escrever num arquivo vigiado
- **Quando** o sensor de alteração dispara
- **Então** nenhum aviso de "outra sessão mexeu" é gerado

#### AC-016 — Alteração de peer aparece na próxima ação

- **Dado** que outra sessão alterou um arquivo com claim meu
- **Quando** esta sessão vai executar a próxima ação
- **Então** o aviso aparece para o modelo naquele momento, com o arquivo e o horário da alteração

### US-005 — O que 5 dias de uso real mostraram (17/09)

Como dono da máquina, quero que o registro de recursos descreva a realidade: claim sobre arquivo que
existe, liberado quando o turno acaba, e com a faixa onde o dono está agora.

#### AC-019 — Fragmento de comando não vira claim

- **Dado** um comando cujo alvo de escrita é variável não expandida (`$STATE_FILE`, `%TEMP%`), pedaço de código (`).write(...)`, `, html)`) ou tem caractere que o Windows proíbe em nome de arquivo
- **Quando** o classificador processa o comando
- **Então** nenhum recurso de arquivo é criado para esse alvo
- **E** caminho legítimo com espaço, acento, parênteses, nome curto 8.3 ou UNC continua virando claim

#### AC-020 — Caminho entre aspas com espaço vira um claim, o do arquivo real

- **Dado** um comando de escrita (`sed -i`, `Set-Content -Path`) cujo alvo está entre aspas e contém espaço
- **Quando** o classificador extrai os alvos
- **Então** sai um único recurso, com o caminho inteiro
- **E** um comando com vários arquivos continua gerando um recurso por arquivo

#### AC-021 — Reentrada de Stop também libera os claims do turno

- **Dado** claims de escopo `turn` desta sessão e um `Stop` com `stop_hook_active` verdadeiro (outro hook de Stop bloqueou e o turno continuou)
- **Quando** o hook de fim de turno roda
- **Então** os claims de turno desta sessão são liberados do mesmo jeito, e a saída continua silenciosa
- **E** claims de outra sessão não são tocados

#### AC-022 — Claim órfão não espera o TTL de ninguém

- **Dado** um claim em disco de sessão morta ou já expirado
- **Quando** uma sessão nova começa
- **Então** esse claim é removido antes de o mapa de peers ser montado
- **E** claim de sessão viva dentro do TTL continua intacto

#### AC-023 — A faixa do claim acompanha o turno inteiro

- **Dado** que o dono já reivindicou um arquivo nas linhas 10-12 e agora edita as linhas 80-84
- **Quando** o claim é renovado
- **Então** o claim em disco passa a listar as duas faixas, e a mais recente vira a principal
- **E** a peer que edita a linha 82 recebe aviso de sobreposição, enquanto quem edita a linha 500 recebe só ciência
- **E** claim sem faixa (`Write`, arquivo inteiro) continua colidindo com qualquer edição

#### AC-024 — Claim de turno não sobrevive ao prompt seguinte

- **Dado** um claim de escopo `turn` desta sessão, tomado no turno anterior e ainda dentro do TTL
- **Quando** o usuário envia o próximo prompt
- **Então** esse claim é liberado antes de o turno novo começar
- **E** a peer que editar aquela faixa em seguida não recebe aviso de sobreposição

#### AC-025 — O release do início do turno não alcança quem não é meu

- **Dado** claims de escopo `turn` de um subagente desta sessão, de outra sessão, e um claim de escopo `session` (perfil de browser) desta mesma sessão
- **Quando** o hook de início de turno roda
- **Então** nenhum dos três é removido
- **E** o claim de turno do main desta sessão é removido na mesma passada

#### AC-027 — Prompt enfileirado antes da entrega não encerra turno nenhum

- **Dado** um `UserPromptSubmit` com origem `poll_event`, que o binário documenta como disparado no enqueue — antes de existir o ack de entrega, portanto com o turno anterior possivelmente em andamento
- **Quando** o hook de início de turno roda
- **Então** nenhum claim é liberado
- **E** com origem do composer do usuário (`user`) o release acontece normalmente
- **E** com o campo `source` ausente — o estado medido deste build — o release acontece normalmente, senão o hook seria instalado e inerte
- **E** origem `system` (mensagem de peer, notificação de tarefa) TAMBÉM libera: medido no transcript que essas entradas viram turno próprio, com `promptId` e `Stop` próprios em sequência, então quando uma delas chega o turno anterior já terminou

#### AC-026 — O hook de início de turno nunca bloqueia nem fala

- **Dado** um payload vazio, lixo que não é JSON, sem `session_id`, ou uma raiz de estado ilegível
- **Quando** o hook de início de turno roda
- **Então** ele termina com exit 0, stdout vazio, e o prompt do usuário segue
- **E** com dono indeterminável (`session_id` vazio) nenhum claim é removido

## Fora de escopo

- Resolver o merge de trabalho concorrente: a feature evita a colisão, integrar continua sendo decisão do Vinicius.
- Coordenação entre máquinas e entre WSL × Windows nativo.
- Broker HTTP próprio: o canal de mensagem já é nativo do harness.
- Statusline: a visibilidade sai pelo comando `ccoord status`.

## Suposições

- **ASM-001** Código em `~/dev/cc-coord` (git próprio) e estado em `~/.claude/coord/`, separados para que sweep de retenção do `.claude` não leve o código.
- **ASM-002** Python 3.14 global (`C:\Python314`), stdlib apenas — hook não é lugar de instalar dependência.
- **ASM-003** O schema de `~/.claude/sessions/<pid>.json` não é documentado. Medido em 11/09: `pid`, `sessionId`, `cwd`, `startedAt`, `procStart`, `version`, `peerProtocol`, `peerFeatures`, `kind`, `pidDomain`, `messagingSocketPath`, `name`, `nameSource`, `nameSince`, `updatedAt`, `status`, `statusUpdatedAt`. Ler defensivamente; nunca falhar por chave ausente.
- **ASM-004** Os hooks entram no `settings.json` global: a coordenação é da máquina, não de um repositório.
- **ASM-005** Resolvida em 04/09: o wrapper no perfil do PowerShell nomeia as sessões automaticamente.
- **ASM-006** O aviso só alcança a sessão nos instantes em que um hook dispara — não existe canal contínuo. O protocolo precisa caber nessas janelas.

- **ASM-007 (BLOQUEANTE)** `UserPromptSubmit` dispara quando o prompt é submetido ao modelo, não quando o usuário aperta Enter com um turno ainda em andamento. Se disparar no Enter, o release cai no meio de um turno vivo — o mesmo erro das três tentativas de 17/09. Medir com `tools/probe/` antes de escrever o hook; não deduzir do nome do evento.
- **ASM-008** `UserPromptSubmit` não dispara dentro de subagente. Se disparar, o `agent_id` do payload precisa entrar na identidade, senão um subagente libera o claim do main.
- **ASM-009** Sem `session_id` no payload nem em `CLAUDE_CODE_SESSION_ID`, o dono sai vazio — e dono vazio casaria com claim de dono vazio de qualquer sessão. Nesse caso o hook não libera nada (AC-026).

## Perguntas em aberto

- **Q-007** O `Stop` libera tudo (inclusive subagente) e o `UserPromptSubmit` poupa subagente. A assimetria é deliberada, mas deixa o claim de subagente para o TTL quando o `Stop` nunca roda limpo. Medir no regime novo; se for material, vira spec junto da pendência 3.
- **Q-008** O hook novo entra no `settings.json` global, que afeta todas as sessões abertas: instalar exige OK do Vinicius e aviso às peers, mesma fronteira da T-012.
- **Q-003** `crossSessionInbound` fica em `accept` global? Sob `bypassPermissions` o padrão é `hold`, e a mensagem entre sessões pode ficar presa esperando clique. Verificar depois da instalação.
- **Q-005** Derivar a faixa de linhas do trecho editado custa uma leitura de arquivo no caminho quente. Se o custo passar de 150 ms, a claim cai para o arquivo inteiro.
- **Q-006** Quantos caminhos vigiados o sensor aguenta antes de pesar? Medir com 10 e 100.
