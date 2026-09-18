# cc-coord

Coordenação entre sessões simultâneas do Claude Code na mesma máquina.

Se você roda duas ou três sessões ao mesmo tempo, elas não sabem uma da outra. Uma reescreve o arquivo
que a outra está editando, o `git commit` arrasta trabalho alheio no meio do turno, dois servidores
disputam a porta 4321 e você valida um número contra o processo errado. O caso que dói mais é o
browser: uma sessão relança o Chrome, o `--user-data-dir` já está travado, e o formulário meio
preenchido da outra morre em `about:blank`.

O `cc-coord` faz as sessões se enxergarem. Ele registra quem está em qual arquivo, porta, repositório
ou browser, e avisa antes que a colisão aconteça.

## A política

**Avisar sempre, bloquear só o irreversível.**

O que evitou colisão na prática não foi exclusão mútua: foi anunciar arquivo e faixa de linhas cedo, e
usar edição cirúrgica em vez de reescrita. Então o padrão para arquivo é `warn` — a sessão recebe o
mapa de quem está onde e decide. `deny` fica reservado para o que não dá para desfazer.

O critério de sucesso é **trabalho jogado fora igual a zero**, não "a ação foi bloqueada". Atrito que
evita perda é aceitável. Atrito que só irrita é defeito.

## O que 5 dias de uso real mostraram

Medição de 17/09/2026 sobre `~/.claude/coord/events.log`, de 12/09 11:47 a 17/09 12:00:

| | |
|---|---|
| eventos | 6.739 |
| claims tomados / liberados | 3.934 / 2.679 |
| recuperados por expiração | 61 |
| disputas (recurso já com dono vivo) | 55 |
| erros | 10 |
| sessões distintas | 35 |

Uma ressalva que importa para ler esse log: `event: "deny"` **não** é bloqueio de ferramenta. Ele sai
quando a *aquisição* falha porque o recurso já tem dono vivo. Quem decide a ferramenta é `policy.py`, e
para arquivo a resposta é `warn`. Contar os "deny" como "o gate barrou" superestima o atrito em cerca
de 100%.

Os quatro defeitos que o uso revelou não haviam aparecido em 366 testes nem em 18 critérios de aceite.
Apareceram no log. Dois deles valem como aviso a quem for construir coisa parecida:

- 13,7% dos ids de recurso não eram caminho, e sim fragmento do próprio comando (`$STATE_FILE` não
  expandido, `/dev/null)`, pedaços de `console.log`). O dano não é o desperdício: é que um em cada
  sete registros ser lixo ensina a ignorar o aviso, e é assim que um gate morre.
- Caminho com espaço virava três claims errados e nenhum certo, porque o parser fazia `split()` cru.
  Gate cego, não ruidoso, em toda a família de caminho com espaço — que na minha máquina é a regra
  ("Área de Trabalho", "Program Files").

## Como funciona

Nove hooks do Claude Code alimentam um registro em disco:

| Hook | Momento |
|---|---|
| `coord_session_start` | injeta o mapa de sessões vivas e recursos ocupados |
| `coord_pre_bash` / `coord_pre_write` | reivindica o alvo antes da escrita |
| `coord_pre_browser` | disputa o perfil do browser |
| `coord_user_prompt` | renova o claim do turno |
| `coord_post_batch` / `coord_file_changed` | acompanha o que mudou |
| `coord_stop` / `coord_session_end` | libera o que era do turno e da sessão |

O estado fica em `~/.claude/coord/` e em `~/.claude/sessions/<pid>.json` (nome, cwd, status). Não há
servidor: é arquivo em disco com lease e expiração, o que significa que uma sessão morta não trava
recurso para sempre.

## Instalar

```
python -m ccoord.install
```

O instalador registra os hooks no `settings.json`. Ele foi feito para Windows com Git Bash, que é onde
roda e onde foi medido.

## Comandos

```
ccoord status          # sessões vivas e recursos ocupados
ccoord who <recurso>   # quem detém, ou se está livre
ccoord release         # libera claims
ccoord sweep           # remove claims de dono morto
```

## Limites

Isto resolve colisão entre sessões do Claude Code **na mesma máquina**. Não é lock distribuído, não
coordena máquinas diferentes e não substitui o git para trabalho concorrente de verdade. Lock preso
também não é o mesmo que peer trabalhando: processo recente quer dizer em uso, e idade alta com
ninguém conseguindo abrir quer dizer órfão. Matar processo alheio continua sendo decisão de quem está
na frente do computador, não do agente.

## Testes

```
python -m pytest -q
```

366 testes e 171 subtests. O teste de p95 do caminho quente é sensível a carga da máquina: se ele
falhar com outros processos pesados rodando, rode isolado antes de concluir que houve regressão.
