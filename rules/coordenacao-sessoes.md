# Coordenação entre sessões simultâneas

O Vinicius roda várias sessões ao mesmo tempo. O custo a eliminar é **trabalho jogado fora** — não a
ação bloqueada. Atrito que evita perda é aceitável; atrito que só irrita é defeito.

## A postura

**Avisar sempre, bloquear só o irreversível.** O que de fato evitou colisão na prática (medido em
11/09, duas sessões dividindo o mesmo `web/app.js` sem um único conflito) foi **anunciar arquivo e
faixa de linhas logo no começo** e usar `Edit` cirúrgico em vez de `Write`. Não foi exclusão mútua.

## No começo do trabalho

1. `ListAgents` antes da primeira edição — saber quem mais está vivo e em qual diretório.
2. Ao pegar um arquivo compartilhado, anunciar por `SendMessage`: **arquivo + faixa de linhas + o que
   vai mudar**. Faixa, não só o arquivo: duas sessões cabem no mesmo arquivo em regiões distintas.
3. Ao tocar em `~/.claude/settings.json`, `~/.claude/hooks/`, `~/.claude/rules/` ou no `MEMORY.md`,
   avisar **antes**. São recursos da máquina inteira, não do repo.
4. No `MEMORY.md`: só `Edit` pontual de uma linha. Nunca reescrever o arquivo.

## Quando o recurso está com outra sessão

| Colisão | O que fazer, em ordem |
|---|---|
| **Browser ocupado** | 1) tentar o outro servidor (`playwright` ↔ `playwright-b`) 2) `SendMessage` + `notify_when_idle` 3) fazer a parte offline por `curl`/`urllib`. **Nunca** matar o processo, nunca `browser_close` (deixa o lock) |
| **Porta ocupada** | subir em porta livre própria. Para validar **valor** (não layout), usar servidor próprio ou produção — jamais os números de servidor alheio |
| **Arquivo em uso** | faixas disjuntas: seguir, com `Edit` cirúrgico. Faixas sobrepostas: pedir a mudança à dona por `SendMessage`, ou integrar por contrato. Se urgente e ela ocupada, escalar ao Vinicius com o custo de cada caminho |
| **Git write** | antes do commit: `git --no-optional-locks log origin/<br>..HEAD` e `git status --short`. Arquivo com mudança de duas sessões não tem commit seletivo: levar ao Vinicius as duas saídas (corrigir o alheio × commitar com bug conhecido). A decisão é dele |
| **Migração de schema** | adiar a migração e implementar só o que não toca schema |

## As três lições que custaram caro

- **Lock preso ≠ peer trabalhando.** Já quase matei processo achando que estava travado, com alguém
  navegando do outro lado.
- **Coordenador morto ≠ recurso órfão.** A sessão que coordenava o recurso ter encerrado não diz nada
  sobre o recurso. Quem diz é o recurso: processo **recente** = em uso; **idade alta + ninguém
  conseguindo abrir** = órfão. O MCP mantém o processo vivo depois do `browser_close`, então presença
  de processo não prova uso, e ausência de sessão desenhando não prova abandono.
- **Dica de peer é pista, não endereço.** O peer relata o estado de quando ele viu, e sessão morta não
  se anuncia. Já recebi de peer o nome de uma sessão que havia encerrado. A verdade é o registro em
  disco (`~/.claude/sessions/`) mais o `ListAgents` — confira antes de agir.

## Kill de processo

Nunca decida por conta. Mesmo com todos os sinais apontando para órfão, a autorização é do Vinicius —
e **não se pede a uma peer** (uma sessão não autoriza a outra a destruir; isso é lavar permissão).
Pergunte a ele, com os sinais medidos na mão: quantidade de processos, idade do mais novo, e quem
está falhando ao abrir.

## Escalar para o Vinicius

Quando os dois caminhos custam trabalho e a escolha é dele: apresentar **as duas saídas medidas** e o
custo de cada uma, não a pergunta crua. Ele decide; eu executo.
