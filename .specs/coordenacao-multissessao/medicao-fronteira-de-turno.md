# Quanto vale a pendência 1 — medida no log + nos transcripts (17/09/2026, fim do dia)

**Veredito: a pendência 1 é real, mas é um quinto do que o comentário em `coord_stop.py` descreve, e o
cenário do teto de 32 faixas nunca aconteceu.** 20,1% dos claims de turno sobrevivem ao fim do turno;
o dano observável em 5 dias foram **10 disputas** contra dono de turno passado (~2/dia), todas com
janela de 1 ou 2 turnos.

Fonte: `~/.claude/coord/events.log` (8.266 eventos, 12/09 a 17/09) cruzado com os 383 transcripts
JSONL de `~/.claude/projects/` (1.683 prompts reais do usuário, 47 sessões casadas).

## Por que precisou de outra fonte

O `events.log` **não tem fronteira de turno**. Nenhum evento diz "o turno acabou": o `release` só
aparece quando o `Stop` roda sem reentrada, que é justamente o que falha. Medir vazamento pelo log
sozinho é medir o defeito com o instrumento que o defeito quebra.

A fronteira de turno veio do transcript: um registro `type: "user"` que não é `isMeta`, não é
`isSidechain`, não carrega `tool_result` e não começa com `<` (system-reminder). O `session_id` do
claim é o nome do arquivo JSONL — o casamento é exato, não heurístico.

## Dois erros de grão corrigidos no caminho

1. **`acquire` no log inclui renovação.** Os 4.766 `acquire` são 3.437 aquisições novas + 1.329
   renovações. Comparar `acquire` com `release` cru — que é como se chegou ao número de "1.140 claims
   presos" em 17/09 de manhã — **superestima o vazamento**, porque conta como aquisição o que é o mesmo
   claim sendo renovado. Ciclos realmente reabertos sem release no meio: **3**.
2. **Sair por `release` não prova que o claim não vazou.** `release(scope="turn")` apaga todos os
   claims de turno do dono de uma vez, então um claim que atravessou três turnos e saiu no `Stop` do
   quarto aparece no log como saída limpa. 93% dos que atravessam fronteira saem por `release`.

## O que os dois lados juntos mostram

| | |
|---|---|
| ciclos de claim de turno casados com transcript | 3.368 |
| atravessam ≥ 1 prompt do usuário | **678 (20,1%)** |
| … dos quais atravessam exatamente 1 | 607 (89,5% dos que atravessam) |
| atravessam 2 | 49 |
| atravessam ≥ 3 | 22 (máximo observado: **20**) |
| renovações por ciclo | p50 **0**, p90 1, máx 44 |
| `ranges` observado nas renovações | máximo **5** (teto é 32) |
| claims em disco agora | 6, o mais velho com 5 min |

**O teto de 32 faixas nunca foi encontrado.** A degradação para "arquivo inteiro" descrita como
consequência ("após 32 turnos o teto estoura") é projeção, não medição: o maior claim observado em 5
dias juntou 5 faixas, e o recordista de fronteiras atravessou 20 turnos com 14 renovações.

## O dano, em unidade de dano

Disputas registradas (`event: "deny"`, que é falha de aquisição, não bloqueio de ferramenta):

| | |
|---|---|
| total | 65 |
| intra-sessão (main × subagente — é a pendência 3, não esta) | 16 |
| contra dono no **mesmo turno** (aviso correto, a feature funcionando) | 40 |
| **contra dono de turno já encerrado (aviso falso)** | **10** |

As 10 tinham janela de 1 turno (6) ou 2 turnos (4). Em 5 dias de uso pesado: **~2 avisos falsos por
dia**.

Limite honesto desta contagem: `deny` só registra falha de *aquisição*. O ruído descrito na pendência
("a peer ouve 'colide'") é decisão do `policy`, que devolve `warn` e **não vai ao log** — então 10 é
piso, não total. O que não dá para fazer é inventar o número de cima.

## Onde o vazamento se concentra

| recurso | ciclos que atravessam |
|---|---|
| `memory/MEMORY.md` | 32 |
| `Projetos/Inteligência de Mercado - distribuidores.md` (vault) | 13 |
| `memory/feedback_medir_antes_de_afirmar.md` | 9 |
| `memory/feedback_gate_falso_positivo_causa.md` | 8 |
| demais arquivos de `memory/` | 6, 6, 4, 4 … |

É a pasta de memória e o vault — exatamente os recursos que várias sessões tocam e que nenhum
`git status` cobre. O hotspot bate com as 18 disputas em `MEMORY.md` vistas na medição da manhã.

## Composição por origem

41,8% das aquisições novas de claim de turno (1.435 de 3.437) vêm de **subagente** (`agent_id` não
nulo). Isso importa para o desenho da correção: `release(owner, scope="turn")` com o dono do main
(`agent_id=None`) casa por `session_id` apenas e **apaga também os claims de subagente** — o que no
`Stop` é inofensivo (o turno acabou para todos) mas no `UserPromptSubmit` pode alcançar um subagente
de background ainda vivo.
