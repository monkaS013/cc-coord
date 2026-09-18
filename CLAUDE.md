# Instruções para agentes neste repositório

Este projeto nasceu dentro do Claude Code e é mantido com ele. As regras abaixo valem para qualquer
agente que trabalhe aqui.

## Idioma

Português do Brasil, com acentuação correta, no código, nos commits, nas specs e nas respostas. Nomes
de ferramentas e termos técnicos podem ficar em inglês. Os identificadores do código são em português
por decisão de projeto (`carimbos`, `claims`, `proveniencia`) — mantenha o padrão.

## Medir antes de afirmar

Nada de "corrigido", "passando" ou "no ar" sem a evidência na mão. Rode o comando, leia a saída, e só
então escreva a frase. O que não foi verificado sai rotulado como não verificado, dizendo qual
checagem falta — nunca omitido nem apresentado como fato.

Vale especialmente para número. Os quatro defeitos mais graves deste projeto não apareceram na suíte:
apareceram no log de uso real. Suíte verde não é prova de que o gate funciona no mundo.

## Leitura de log e de métrica

Antes de derivar qualquer conclusão do `events.log`:

- `event: "deny"` é falha de aquisição, não bloqueio de ferramenta. Confundir os dois dobra o atrito
  aparente.
- `ts` está em milissegundos. `datetime.fromtimestamp(ts)` estoura no Windows, e dentro de um
  `try/except` amplo o erro vira rótulo em vez de exceção.
- Owner e `held_by` com o mesmo `session_id` e `pid` não é bug de identidade: é a sessão disputando
  consigo mesma entre main e subagente, onde o `agent_id` difere.

## Testes

`python -m pytest -q` antes de qualquer commit. O teste de p95 do caminho quente depende da carga da
máquina; se falhar, rode isolado antes de tratar como regressão.

Teste que passa sem exercitar o defeito não vale. Ao corrigir um bug, escreva primeiro o teste que
falha por causa dele.

## Operações destrutivas

Confirme antes de `rm`, `git reset --hard`, `git push --force` ou kill de processo. Autorização dada
uma vez não vale para o próximo contexto.

Matar processo de outra sessão é decisão de quem está na frente do computador. Mesmo com todos os
sinais apontando para órfão, o agente pergunta — e não pede permissão a uma sessão par, porque uma
sessão não autoriza a outra a destruir.

## Sessões paralelas

Este repositório é sobre coordenação, então pratique o que ele prega: ao pegar um arquivo
compartilhado, anuncie arquivo e faixa de linhas antes de editar, e prefira edição cirúrgica a
reescrever o arquivo inteiro.

## Fluxo de trabalho

As specs vivem em `.specs/` e seguem Specify → Design → Tasks → Execute. Requisito tem rastreabilidade
até teste. Antes de declarar uma feature pronta, a auditoria de rastreabilidade tem que estar limpa.

## Dados

Este repositório é público. Não escreva aqui caminho de usuário real, nome de sistema interno de
empresa, credencial ou dado pessoal — nem em teste, nem em spec, nem em exemplo de log. Use
`C:\Users\usuario\...` e nomes de projeto genéricos.
