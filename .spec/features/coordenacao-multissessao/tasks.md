# Tasks: Coordenacao multissessao

> feature: coordenacao-multissessao

<!--
  Camada de enforcement. A quebra comentada, com dependências, ondas e o motivo
  de cada decisão, está em ../../../.specs/coordenacao-multissessao/tasks.md
-->

## T-001 — Medir os payloads reais de hook [concluida]

- Refs: US-002
- Arquivos: tools/probe/capture.py, tools/probe/probe-settings.json, tools/probe/run_probe.sh, tools/probe/run_probe2.sh, tools/probe/run_probe3.sh, tools/probe/run_probe4.sh
- Notas: decidiu a Q-004. `FileChanged` existe, dispara em 0,62 s para escrita externa, dispara também para escrita própria e NÃO tem canal de volta para o modelo. Resultado em `.specs/coordenacao-multissessao/medicao-hooks.md`.

## T-002 — Esqueleto do repo e runner de teste TAP [concluida]
- Refs: AC-001
- Arquivos: onpspec.config.json, tests/run_tap.py, src/ccoord/__init__.py, .gitignore
- Notas: stdlib apenas (ASM-002), então o runner é unittest emitindo TAP 13 — é o que dá granularidade por AC ao `verify`.

## T-003 — sessions.py: descobrir as sessões vivas [concluida]
- Refs: AC-012
- Arquivos: src/ccoord/sessions.py, tests/test_sessions.py
- Notas: só leitura de `~/.claude/sessions/<pid>.json`; liveness por PID + procStart; `status` é rótulo, não decisão; ignorar pidDomain de outro host.

## T-004 — claims.py: claim advisory com TTL [concluida]
- Refs: AC-001, AC-002, AC-008
- Arquivos: src/ccoord/claims.py, tests/test_claims.py
- Notas: exclusão por `os.open(O_CREAT|O_EXCL)`; proibido tmp+rename (EPERM no Windows); unidade é (arquivo, faixa de linha).

## T-005 — classify.py: da chamada de ferramenta para recursos [concluida]
- Refs: AC-004, AC-003, AC-006, AC-007
- Arquivos: src/ccoord/classify.py, tests/test_classify.py
- Notas: função pura; chavear no caminho do arquivo, NUNCA no diretório da sessão.

## T-006 — policy.py: a tabela de decisão [concluida]
- Refs: AC-003, AC-005, AC-017, AC-006, AC-007, AC-010
- Arquivos: src/ccoord/policy.py, tests/test_policy.py
- Notas: avisar sempre, recusar só kill e git write; kill nunca decide por idade — recusa e manda perguntar ao Vinicius.

## T-007 — hookio.py: falar com o harness sem quebrar o turno [concluida]
- Refs: AC-011, AC-009, AC-013, AC-014
- Arquivos: src/ccoord/hookio.py, tests/test_hookio.py
- Notas: formatos confirmados na T-001; identidade = (session_id, agent_id); systemMessage não chega ao modelo.

## T-008 — cli.py: status, who, release, sweep [concluida]
- Refs: AC-012
- Arquivos: src/ccoord/cli.py, tests/test_cli.py
- Notas: visibilidade sob demanda; não mexe no statusline do monitor de uso.

## T-009 — Entrypoints de hook [concluida]
- Refs: AC-015, AC-016, AC-008
- Arquivos: hooks/coord_session_start.py, hooks/coord_pre_write.py, hooks/coord_pre_bash.py, hooks/coord_post_batch.py, hooks/coord_file_changed.py, hooks/coord_stop.py, hooks/coord_session_end.py, tests/test_entrypoints.py
- Notas: o de Stop sai calado; o de SessionEnd não emite invólucro; o sensor filtra o eco da escrita própria.

## T-010 — A política escrita [concluida]
- Refs: AC-003, AC-005
- Arquivos: rules/coordenacao-sessoes.md
- Notas: quando desviar, quando esperar, quando negociar, quando escalar. Inclui "lock preso ≠ peer trabalhando" e "coordenador morto ≠ recurso órfão". Entregável documental — prova no ensaio da T-013.

## T-011 — Custo do caminho quente abaixo de 150 ms [concluida]
- Refs: AC-005, AC-016
- Arquivos: tests/test_perf.py
- Notas: medir com 3, 10 e 30 claims; decide a Q-005 e a Q-006.

## T-012 — Instalador dos hooks [concluida]
- Refs: AC-012
- Arquivos: src/ccoord/install.py, tests/test_install.py
- Notas: SÓ RODA APÓS APROVAÇÃO DO VINICIUS — mexe no settings.json global, que afeta todas as sessões abertas. Backup datado e merge que preserva os hooks existentes.

## T-013 — Ensaio ponta a ponta com duas sessões reais [concluida]
- Refs: AC-003, AC-012, AC-016
- Arquivos: tools/ensaio/roteiro.md, tools/ensaio/resultado.md, tools/ensaio/rodar_cenarios.py
- Notas: 8 de 8 cenários PASS, o 7 com desfecho diferente do previsto (quem destrava não é a fila entre peers — é o Vinicius, rodando por `!`, que não passa pelo PreToolUse). Verificação por grep no transcript JSONL: o modelo é testemunha não confiável do que recebeu, e isso se confirmou duas vezes (uma sessão afirmou "nenhum aviso apareceu" com o aviso no transcript, e outra afirmou ter sido bloqueada num caso em que o gate nem rodou). Achou 5 defeitos que 233 testes e 17/17 ACs não acharam — ver T-019, T-020, T-021 e resultado.md.

## T-017 — Fazer o caminho quente caber em 150 ms [concluida]
- Refs: AC-005, AC-016
- Arquivos: hooks/coord_pre_write.py, hooks/coord_pre_bash.py, hooks/coord_post_batch.py, src/ccoord/hookio.py, tools/bench_hooks.py, tests/test_perf.py
- Notas: a T-011 reprovou o RNF-04 (p95 213-227 ms contra limite de 150). Causa medida: custo fixo de import — 94 ms p50 para subir o Python e importar 4 módulos, contra 31 ms de interpretador vazio, sobrando ~56 ms para lógica. Saída: fast path que decide o caso comum (sem peer viva / sem claim no recurso) lendo dois diretórios, com import tardio do resto. PROIBIDO afrouxar o limite — é requisito do dono; se não couber, a decisão sobe para ele. O fast path precisa de teste provando que não engole conflito real, senão troca lentidão por gate cego.

## T-016 — Citar o commit alheio de verdade no deny de git [concluida]
- Refs: AC-007
- Arquivos: hooks/coord_pre_bash.py, tests/test_entrypoints.py
- Notas: `policy.py` já lê `contexto["commits_alheios"]`, mas NENHUM entrypoint preenche esse campo — o `deny` sai por presença de peer viva no repo (a proteção funciona), só que sem citar o commit, que é metade do que o AC-007 pede. O hook precisa rodar `git --no-optional-locks log origin/<branch>..HEAD --oneline` e passar o resultado. Descoberto em 11/09 DEPOIS de o audit ficar verde: o AC tinha prova PASS no teste do módulo, que recebe o contexto pronto — teste de componente não prova o caminho ponta a ponta.

## T-018 - Carimbos de escrita propria numa fonte unica [concluida]

- Refs: AC-015, AC-016
- Arquivos: src/ccoord/carimbos.py, hooks/coord_pre_bash.py, tests/_guarda.py, tests/__init__.py
- Notas: achado ALTA da 4a auditoria (12/09). O ramo novo de escrita-de-arquivo-via-Bash nao carimbava a escrita propria, entao o `FileChanged` seguinte deixava de ser reconhecido como eco e a sessao recebia aviso FALSO de "mudou em disco por outro processo" sobre a propria escrita. A funcao vivia duplicada em dois hooks (duplicacao consciente da T-09, que so podia tocar em `hooks/`); virou `ccoord.carimbos`, fonte unica -- duas implementacoes de `slug_path` divergindo em um caractere fariam o eco nunca casar, e o sintoma nao apontaria para a causa. Junto: `tests/_guarda.py` fecha o buraco de isolamento que o `tests/__init__.py` nao pegava (`unittest discover -s tests` sem `-t` carrega os modulos soltos e nunca importa o pacote -- medido, vazou 2 linhas para o events.log real).

## T-019 - Mesmo arquivo, duas grafias de caminho, mesmo id [concluida]

- Refs: AC-004, AC-005, AC-015, AC-016
- Arquivos: src/ccoord/paths.py, src/ccoord/classify.py, src/ccoord/carimbos.py, src/ccoord/sessions.py, hooks/coord_pre_write.py, hooks/coord_file_changed.py, tests/test_classify.py, tests/test_entrypoints.py
- Notas: achado do ENSAIO com duas sessoes reais (T-013, 12/09) -- nao dos 233 testes nem dos 17/17 ACs. Uma sessao segurava `...VINICI~1...\compartilhado.py` e a outra editava `...ViniciusMoraisHDT...\compartilhado.py`: `os.path.samefile` = True, ids diferentes, gate CEGO. Nao e hipotetico -- o diretorio de scratchpad entregue a cada sessao vem no formato 8.3, entao valeria para quase toda sessao. `ccoord.paths.resolver_nome_curto` e a fonte unica (unica consulta ao SO no caminho de classificacao, so quando ha `~<digito>`, memoizada); ligada em classify, carimbos, sessions (o `cwd` da peer tambem e chave) e nos dois hooks que ainda tinham copia local de `slug_path` -- a "fonte unica" da T-018 tinha ficado pela metade, e a metade que faltava foi exatamente a que quebrou. Sintoma didatico: 7 testes falharam apontando "o aviso sumiu" quando o codigo estava certo e a COPIA no proprio teste e que estava velha. Fica aberto de proposito: unidade mapeada x UNC.

## T-020 - Eixo de proveniencia: decisao minha x ordem do Vinicius [concluida]

- Refs: AC-018, AC-009, AC-010
- Arquivos: src/ccoord/proveniencia.py, src/ccoord/policy.py, hooks/coord_pre_bash.py, tests/test_proveniencia.py, tests/test_entrypoints.py
- Notas: defeito medido no ensaio T-013 -- o Vinicius mandou `taskkill /F /PID 20448` com todas as letras e o gate devolveu deny com a razao terminando em "pergunte ao Vinicius", pedindo autorizacao a quem deu a ordem. Atrito puro: o desfecho previsivel e ele matar por fora, sem gate e sem registro, e a peer perde o trabalho do mesmo jeito. NAO afrouxa o deny -- separa kill nascido de inferencia minha (segue deny) de kill que o usuario NOMEOU no turno (vira warn com o custo explicito). Autoriza o ALVO, nao a vontade: exige verbo de kill + alvo casando com o recurso, com fronteira de palavra (`process:448` nao casa dentro de `20448`). Duas travas contra auto-autorizacao: `tool_result` chega no transcript com `role="user"` e e descartado (senao a saida de um comando meu viraria ordem dele), e transcript ausente/ilegivel devolve "" = segue deny. `estado_ilegivel` continua vencendo a proveniencia: ali nao da para saber se existe dono. Custo zero no caminho quente -- so o ramo de kill le transcript, e le so os ultimos 64KB.

## T-021 - Correcoes que so o uso expos: o gate atrapalhando quem faz certo [concluida]

- Refs: AC-007, AC-005, AC-017
- Arquivos: src/ccoord/policy.py, src/ccoord/classify.py, hooks/coord_pre_bash.py, tests/test_proveniencia.py, tests/test_entrypoints.py, tools/ensaio/resultado.md
- Notas: quatro defeitos achados pelo ensaio T-013 com sessoes reais, nenhum por teste. (1) commit com PATHSPEC explicito sem interseccao com claim de peer viva vira `warn` -- antes `git add <arquivo proprio> && git commit` recebia o mesmo deny de `commit -am`, punindo quem fazia certo; o pathspec ignora o indice, entao e seguro por CONSTRUCAO e nao depende de medir estado compartilhado que expira em segundos (`-a` anula, `push` segue deny). (2) a razao do deny prescrevia duas saidas inexecutaveis -- "finalize numa branch propria" nao destrava (a condicao e peer-viva-no-repo) e "aguarde a peer liberar" nao e mecanismo (peer nao levanta gate do usuario); uma sessao gastou dois turnos descobrindo isso. Agora a razao nomeia o destravamento real: levar ao Vinicius, que roda por `!`. (3) o aviso pedia "mande SendMessage ANTES de editar", impossivel de cumprir porque o `additionalContext` de PreToolUse que nao bloqueia chega JUNTO com o resultado da ferramenta -- texto reescrito no passado. (4) peer na HOME contava como "no mesmo repositorio" (corrigido durante o ensaio). Produtor testado no entrypoint, nao so o consumidor em `policy` -- o buraco que ja tinha mordido AC-007 e AC-010.

## T-022 - Posse do perfil de browser: fechar a colisao que ORIGINOU o projeto [concluida]

- Refs: AC-003, AC-009, AC-011
- Arquivos: src/ccoord/classify.py, src/ccoord/policy.py, src/ccoord/claims.py, src/ccoord/install.py, hooks/coord_pre_browser.py, tests/test_browser.py, tests/test_install.py
- Notas: o catalogo de colisoes que abriu o projeto tem o browser em PRIMEIRO lugar (dois formularios de candidatura perdidos), e era a unica sem cobertura automatica -- o gate recusava o KILL (AC-003) mas o claim que esse ramo consulta NUNCA era adquirido: os matchers do PreToolUse pegavam Edit/Write/NotebookEdit e Bash, e ferramenta de MCP passa ao largo. O deny do cenario 4 do ensaio so funcionou porque criei o claim a mao. Unidade de posse = o SERVIDOR MCP (`playwright`, `playwright-b`), que e quem segura o perfil no disco e devolve "Browser is already in use"; escopo `session`, nao `turn` (o uso atravessa turnos; claim de turno sumiria no Stop no meio da navegacao); `browser_close` vira `release` (nao mata o processo, mas e a unica declaracao honesta de fim de uso) e exigiu `claims.release_resource()`, o caso do meio que faltava entre release-por-turno e release-por-sessao. Ferramentas de LEITURA (screenshot, console, snapshot) nao tomam posse -- sem isso a primeira sessao a espiar travaria as outras. A decisao e sempre `warn`, nunca deny: duas sessoes no mesmo perfil nao destroem trabalho, o que destroi e a segunda matar o processo (esse segue recusado), e o aviso traz a alternativa CONCRETA (o outro servidor). Produtor testado no entrypoint exigindo o claim EM DISCO, nao so a saida do hook.

## T-014 — Fechamento mecânico [concluida]
- Refs: AC-001, AC-002, AC-003, AC-004, AC-005, AC-006, AC-007, AC-008, AC-009, AC-010, AC-011, AC-012, AC-013, AC-014, AC-015, AC-016, AC-017
- Arquivos: .spec/verification/coordenacao-multissessao.json
- Notas: `onp-spec verify` + `audit --ci`; não declarar pronto sem 0 ERRO.

## T-015 — Registro no vault e na memória [concluida]
- Refs: AC-012
- Arquivos: .specs/coordenacao-multissessao/RETOMAR.md
- Notas: Edit pontual no MEMORY.md, nunca reescrita.
