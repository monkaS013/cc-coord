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

## T-023 - Achados da 5a auditoria adversarial: quatro furos no ramo de kill/escrita [concluida]

- Refs: AC-003, AC-005, AC-010
- Arquivos: src/ccoord/classify.py, src/ccoord/policy.py, tests/test_auditoria5.py
- Notas: 12 agentes, 17 achados brutos, 5 sobreviveram a refutacao adversarial; 4 corrigidos aqui, 1 (aliases de git tipo `git ci`) fica como divida por ser hipotetico -- o Vinicius nao tem alias nenhum configurado (`git config --get-regexp ^alias\.` vazio). Modo de falha comum aos quatro: o comando escapava do classificador e `coord_pre_bash.py` faz allow TOTAL quando `recursos == []` -- nao e warn relaxado, e a arvore de decisao inteira pulada. (1) CIM/WMI moderno (`Get-CimInstance | Invoke-CimMethod -MethodName Terminate`, `Get-WmiObject().Terminate()`) nao era kill; `wmic` esta deprecado no Win11, entao era o idioma MAIS provavel de aparecer, e o proprio projeto usa `Get-CimInstance Win32_Process` para contar processo. (2) `Get-Process chrome | Stop-Process` -- o idioma mais comum de PowerShell -- virava `process:desconhecido`, id que nunca casa com o claim real (`browser:chrome`), entao `owner_of` devolvia None = livre e o kill SAIA LIBERADO; agora o nome e extraido do `Get-Process`. (3) Kill sem alvo identificavel virava `process:desconhecido` com allow: escrever o comando de um jeito que o parser nao entendesse era o caminho mais facil para matar processo de peer. Agora e `process:alvo-nao-identificado` e a politica RECUSA -- fail-closed simetrico ao de estado ilegivel: la nao se sabe DE QUEM e o processo, aqui nao se sabe QUAL e. (4) Escrita dentro de subshell (`cmd /c "..."`, `powershell -Command "..."`, `bash -c '...'`) escapava de todos os detectores -- bastava embrulhar para furar o gate; o miolo agora passa pelos mesmos detectores, um nivel de recursao. Junto: o gap de cobertura do `coord_pre_browser.py` (nenhum cenario comecava com claim de peer JA existente, entao remover a guarda deixava a suite verde) -- teste novo, mutante provado morto com restauracao por sha256.

## T-024 - O alvo de escrita precisa ser um arquivo [concluida]

- Refs: AC-019, AC-020
- Arquivos: src/ccoord/classify.py, tests/test_t024_alvo_de_escrita.py
- Notas: primeiro conserto vindo do USO, nao de auditoria nem de teste -- 5 dias de events.log (17/09) mostraram 925 de 6.737 ids (13,7%) que nao eram caminho, so pedaco do comando (`$STATE_FILE`, `/dev/null)`, `open('x','w').write(s)`); 8 chegaram a `os.open` e voltaram `[Errno 22]`, 2 geraram disputa contra recurso inexistente. Ruido assim ensina a ignorar o aviso, que e como um gate morre. O filtro (`_alvo_de_escrita_plausivel`) roda antes de virar recurso, so olha TEXTO (nao toca disco: caminho quente do RNF-04) e fica num ponto unico, `_recurso_escrita`, por onde passam os 5 detectores de escrita via Bash. Cada regra tem controle negativo, porque filtro que rejeita demais troca ruido por CEGUEIRA. Achado que nao estava na lista e e pior que o ruido: `_alvos_sed` fazia `split()` cru, entao `sed -i 's/a/b/' "C:/.../Area de Trabalho/nota.md"` virava TRES claims errados e NENHUM do arquivo real -- gate cego em toda a familia de caminho com espaco, que aqui e a regra ("Area de Trabalho", "Program Files", "OneDrive - HDT ENERGY"); mesma falha no `-Path` do PowerShell. Tokenizador proprio em vez de `shlex` (posix=True come a contrabarra do Windows; posix=False devolve a aspa colada). Dois casos da primeira versao do teste (`|` e `<`) foram REMOVIDOS: no shell sao operadores, entao `echo x > saida|pipe.txt` redireciona mesmo para `saida` -- o defeito era do teste, nao do filtro.

## T-025 - Claim de turno nao sobrevive ao turno [concluida]

- Refs: AC-021, AC-022
- Arquivos: hooks/coord_stop.py, hooks/coord_session_start.py, tests/test_t025_vazamento.py
- Notas: 3.934 claims tomados contra 2.679 liberados em 5 dias (1.140 presos), espalhados por TODAS as 35 sessoes -- ~30% do que cada uma toma. Nao e sessao que morre, e sistematico. Causa no codigo, medida e nao inferida: o release estava sob `if not payload.get("stop_hook_active")`, para nao repetir trabalho em reentrada. Mas numa reentrada os claims NAO sao os mesmos: todo Stop bloqueado por outro hook faz o turno continuar, e o que for reivindicado nessa continuacao so sairia num Stop futuro sem reentrada -- e nesta maquina o Stop tem QUATRO hooks de terceiros registrados (verify_gate.py, delta_gate.py, obsidian_stop.py, hook.mjs), tres deles medidos bloqueando, entao reentrada e rotina. `release()` e idempotente e roda fora do caminho quente. A varredura de `own_writes` continua so na primeira passada: e manutencao, nao liberacao de recurso. Para o que ja vazou (51 dos 76 claims em disco com +24h), `claims.sweep()` passou a rodar no SessionStart, antes de montar o mapa -- senao a sessao nova aprende a respeitar dono que nao existe mais. Nao-vacuidade nos dois: o Stop de B nao libera claim de A, e o sweep nao come claim de sessao viva (o teste de nao-vacuidade pegou uma fixture com `proc_start` inventado sendo lida como morta).

## T-026 - A faixa do claim acompanha o turno inteiro [concluida]

- Refs: AC-023
- Arquivos: src/ccoord/claims.py, src/ccoord/policy.py, tests/test_t026_faixas_do_turno.py
- Notas: o item 3 da medicao era "45 das 55 disputas caem na pasta de memoria"; ao abrir, o defeito nao era o volume, era a faixa estar ERRADA. `_renovar()` recriava o claim com `range=existente.range` -- a faixa da PRIMEIRA edicao do turno -- enquanto o events.log registrava a faixa NOVA (log divergindo do disco, que e o que fez a primeira leitura desta mesma investigacao passar perto do defeito sem ve-lo). Efeito nos dois sentidos: silencio indevido para quem edita onde o dono esta AGORA (a colisao real que a feature existe para pegar) e alarme falso para quem edita onde ele ja nao esta. Agora o claim guarda `ranges` (todas as faixas do turno, dedupe, teto de 32 -- ao estourar degrada para arquivo inteiro, que avisa demais e nunca de menos) e `range` passa a ser a mais RECENTE. `Write` (faixa None) zera a lista: vale pelo arquivo inteiro, e dono que ja vale pelo arquivo inteiro nao volta atras. Compatibilidade: claim gravado sem `ranges` (todos os que estao em disco hoje) continua legivel, derivando a lista de `range`. Custo medido: renovacao p50 12,5 ms contra 15,0 ms do HEAD, dentro do ruido, e o gate RNF-04 (p95 < 150 ms no pior caso) segue verde.

## T-027 - Achados da 6a auditoria adversarial sobre T-024/025/026 [concluida]

- Refs: AC-019, AC-020, AC-021, AC-023
- Arquivos: src/ccoord/classify.py, src/ccoord/claims.py, src/ccoord/cli.py, hooks/coord_stop.py, tools/avaliar_filtro.py, tools/corpus_fragmentos.txt, tests/test_t024_alvo_de_escrita.py, tests/test_t025_vazamento.py, tests/test_t026_faixas_do_turno.py
- Notas: dois auditores sobre o diff das tres tasks anteriores; 8 achados corrigidos aqui. (1) **O filtro do AC-019 trocou ruido por CEGUEIRA** -- regras "espertas" (parentese+`;,=`, chamada `\w(`, teto de 260 chars) rejeitavam 5,15% dos arquivos REAIS da maquina, inclusive o vault inteiro (`Plano - portfolio GitHub (plano completo, 2026-08-25).md`), a planilha `SG$A Rateio_Chile.xlsx` e o lock `~$planilha.xlsx`. O controle negativo que usei (o proprio events.log) NAO podia detectar isso: o log so contem alvos que o classificador ja produzia -- GRAO ERRADO invalida a medida. Refeito contra DOIS corpora (736 fragmentos reais x 129.541 arquivos que existem em disco): regra so entra se cegar ZERO real. Conjunto final pega 42% dos fragmentos e cega 0,0000%. Medicao de cada regra descartada: parentese+`;,=` 53 fragmentos/340 reais; `\w(` 68/622; len>260 **0 fragmentos**/7.278 reais. `$VAR` agora so conta como SEGMENTO INTEIRO do caminho (491 casos reais no log: `$WORK`, `$SP`, `$SCRATCH`). O avaliador virou ferramenta versionada (`tools/avaliar_filtro.py`) com o corpus, porque a proxima pessoa a mexer no filtro precisa da tabela dos dois lados, nao da minha intuicao. (2) **Regressao do tokenizador**: `sed -i "s/\"/X/g" a.md b.md` perdia os DOIS arquivos (aspa escapada fechava cedo) e aspa sem par engolia o alvo -- casos que o `split()` cru acertava. Agora `\` seguido da aspa que abriu e escape, e aspa sem par DEGRADA para `split()`. (3) tokenizador aplicado tambem a `_detectar_tee` e `_detectar_copia_move`, que tinham ficado de fora. (4) **A T-025 apagava o que a T-026 acumulava**: release incondicional no Stop removia o claim, e como reentrada NAO e fim de turno, as faixas ja tocadas sumiam no meio do trabalho -- a peer que editasse exatamente onde eu estava ouvia "sem sobreposicao". Corrigido com assimetria: reentrada agora so ENCURTA o TTL para 90 s (`claims.encurtar_ttl_do_turno`, nunca alonga), release de verdade so no Stop que nao e reentrada. Resolve os dois lados sem adivinhar o futuro: turno que continua renova e preserva tudo; turno que acabou morre em 90 s em vez de 900. (5) `claims.overlapping()` e `ccoord status/who` liam so `range` e passariam a discordar do `policy` -- dois oraculos para a mesma pergunta e receita de defeito invisivel. (6) `ranges` malformado (`"xx"`, `[[1]]`, `[['a','b']]`) atravessava `from_dict` e estourava em `policy.decide`, com o `hookio` engolindo a excecao e o aviso SUMINDO em silencio; validacao de aridade/tipo no unico ponto por onde todo claim lido passa. Custo medido do conjunto no caminho quente: classify +0,16 ms, claim +1,8 ms, contra orcamento de 150 ms. **Fica em aberto, medido e nao corrigido:** `_renovar` e leitura-modificacao-escrita nao atomica (2 processos concorrentes perderam 18 de 21 faixas; janela pre-existente, 10,7% aqui contra 14,6% no HEAD -- nao piorou, mas agora o que se perde e ate 32 faixas em vez de 1) e a assimetria main x subagente em `_same_owner_identity`. Os dois sao anteriores a estas tasks e exigem repensar a primitiva de lock; estao no relatorio e vao para decisao do dono, nao para conserto apressado no fim da sessao.

## T-028 - Achados da 7a auditoria: o conserto da T-027 tinha dois defeitos [concluida]

- Refs: AC-020, AC-021
- Arquivos: hooks/coord_stop.py, src/ccoord/claims.py, src/ccoord/classify.py, tests/test_t024_alvo_de_escrita.py, tests/test_t025_vazamento.py
- Notas: auditoria sobre o DELTA da T-027 (o conserto dos achados anteriores, que ninguem tinha verificado -- o gate de delta cobrou e estava certo). Dois achados. (1) **ALTO: `encurtar_ttl_do_turno` trocava exclusao por limpeza.** A hipotese era "a proxima edicao renova e preserva as faixas", e ela so vale se a proxima edicao chegar em menos de 90 s. Medido no events.log real (n=1.981 intervalos entre `acquire` do mesmo recurso/sessao): **mediana 79,4 s e 48,2% acima de 90 s** -- ou seja, em quase metade das vezes o claim expirava no meio do turno, perdia as faixas do mesmo jeito E liberava o recurso para uma peer com o dono VIVO trabalhando (reproduzido com controle negativo). O erro estava na PREMISSA que originou a T-025, nao no conserto: **claim de turno vazado nao bloqueia ninguem** -- passado o TTL ele fica inerte, porque `coord_session_start.py:98` pula expirado ao montar o mapa e `policy` so consulta dono vivo e nao expirado (conferido no codigo, nao assumido). O dano real dos 1.140 claims era acumulo de ARQUIVO em disco, e quem resolve isso e o `claims.sweep()` do SessionStart (AC-022), sem tocar em exclusao. Entao a regra voltou a ser a simples -- reentrada nao mexe em claim nenhum -- e `encurtar_ttl_do_turno` foi REMOVIDA (66 linhas), para nao ficar arma carregada no modulo. A aritmetica dela, alias, estava correta (36 casos testados, zero alongamento, idempotente): o defeito era a decisao de usa-la, nao a implementacao. (2) **MEDIO: apostrofo no meio de nome proprio cegava o alvo.** A protecao de aspa-sem-par so cobria numero IMPAR; com numero PAR o tokenizador pareava `Bob's` com `Ana's` e engolia os dois caminhos num token so, devolvendo ZERO recurso onde o `.split()` antigo achava DOIS -- violando o invariante escrito no proprio docstring ("nunca pior do que o que ja havia"). 136 arquivos desta maquina tem apostrofo no caminho. Corrigido com a regra do shell de verdade: **aspa so ABRE citacao no INICIO de um token**. Nota de metodo que vale registrar: `tools/avaliar_filtro.py` chama `_alvo_de_escrita_plausivel` direto, entao mede o FILTRO e nao o pipeline -- nao teria como reprovar um defeito de tokenizacao. E o mesmo erro de grao que reprovou o events.log como controle negativo, agora na ferramenta que nasceu para consertar aquele erro. Sem achado em: aritmetica do TTL isolada (36 casos), excecoes nos caminhos novos (78 chamadas), filtro cegando caminho real (127.829 arquivos + 30 comandos).

## T-036 - Subir N do gate RNF-04 de 20 para 60 [concluida]

- Refs: AC-024
- Arquivos: tests/test_perf.py
- Notas: o p95 de 20 amostras e o 19o valor ordenado -- praticamente o segundo maior, entao um unico outlier de carga reprovava o gate (visto o dia 17/09 inteiro, inclusive no HEAD limpo e inclusive com o codigo novo medido MAIS RAPIDO em medicao pareada). Com N=60 o p95 e o 57o de 60. **O limite de 150 ms NAO foi tocado** -- e requisito do Vinicius; mudou a precisao da estimativa, nao o criterio. Medido depois: 5/5 verde isolado e 4 de 5 rodadas da suite CHEIA limpas, contra reprovacao em toda rodada cheia antes. Nao e "resolvido": e menos fragil, e o p95 absoluto continua rondando os 150 ms nos dois lados, o que e questao do projeto (ver medicao-uso-producao.md).

## T-030 - Medir QUANDO o UserPromptSubmit dispara (ASM-007, bloqueante) [em-andamento]

- Refs: AC-024, AC-027
- Arquivos: tools/probe/probe-settings.json, .specs/coordenacao-multissessao/medicao-hooks.md
- Notas: primeira task e uma MEDICAO, nao codigo, porque a pendencia 1 ja foi resolvida errado tres vezes e as tres o erro estava na PREMISSA. A leitura do binario (build 2.1.261, `medicao-hooks.md` 5-bis) ja refutou metade das suposicoes antes de qualquer codigo: `UserPromptSubmit` dispara para SEIS origens, `poll_event` dispara NO ENQUEUE (turno possivelmente em andamento) e `system` inclui mensagem de peer -- o canal que esta propria feature usa. Falta o que so runtime responde: (1) este build envia o campo `source` no composer interativo? O binario avisa "payloads may omit it while the field rolls out", e sem o campo a regra `== "user"` deixa o hook INERTE; (2) com `source == "user"`, o evento chega mesmo depois do turno anterior morrer, ou no Enter de uma mensagem enfileirada? (3) o evento dispara dentro de subagente (ASM-008, ja refutada no binario -- confirmar em runtime). Roteiro: probe capturando `UserPromptSubmit`/`PreToolUse`/`Stop` com timestamp; enfileirar mensagem durante um turno longo; `SendMessage` de uma peer no meio do turno; ler a ORDEM real. Reprovou -> a spec morre e a pendencia 1 volta a ser divida declarada, com o motivo medido.
- **MEDIDO em 17/09 (rodada 5 do probe, headless `-p`): o payload de `UserPromptSubmit` neste build NAO TRAZ o campo `source`.** Campos presentes: `session_id`, `transcript_path`, `cwd`, `prompt_id`, `permission_mode`, `hook_event_name`, `prompt`, `session_title`. Nem `source` (que deveria ser `"sdk"` em headless) nem `agent_id`. O binario avisava -- "payloads may omit it while the field rolls out" -- e e o que acontece. **Consequencia: a regra `source == "user"` do desenho deixaria o hook INERTE**, instalado e sem efeito nenhum, que era exatamente a armadilha registrada na spec. Falta medir em sessao INTERATIVA (o composer pode enviar o campo onde o headless nao envia) e o caso da mensagem enfileirada. Abre a alternativa da T-037, que dispensa o campo e o hook.

## T-031 - release(agente_exato=True): nao alcancar subagente [concluida]

- Refs: AC-025
- Arquivos: src/ccoord/claims.py, tests/test_claims.py
- Notas: `_same_owner_identity(a, b, casar_agent=True)` casa so por `session_id` quando `b.agent_id` e nulo -- entao o release do main apaga TAMBEM os claims de subagente (41,8% das aquisicoes de turno). No `Stop` isso e inofensivo (o turno acabou para todos); no inicio do turno pode alcancar subagente de background vivo, que e SILENCIO INDEVIDO, o modo de falha pior desta feature. Parametro keyword-only com default `False` para que `Stop`/`SessionEnd` fiquem byte a byte como estao. Nao-vacuidade obrigatoria: teste que prova que com `agente_exato=False` o claim de subagente SAI (senao o teste passa sem a mudanca existir).

## T-032 - Hook de inicio de turno [concluida]

- Refs: AC-024, AC-026, AC-027
- Arquivos: hooks/coord_user_prompt.py, src/ccoord/install.py, tests/test_t032_inicio_de_turno.py, tests/test_install.py
- Notas: depende de T-030 (ASM-007) e T-031. Faz UMA coisa, e so quando a origem e o composer do usuario (AC-027): `claims.release(identidade(payload), "turn", agente_exato=True)`, exit 0, stdout VAZIO. Stdout vazio nao e estetica: o binario monta `additionalContext` SOZINHO a partir de qualquer stdout nao vazio com exit 0, so neste evento e no `UserPromptExpansion` -- um `print()` de depuracao esquecido vira token gasto em TODA mensagem do usuario. E exit 2 BLOQUEIA o prompt ("Prompt blocked: the UserPromptSubmit hooks did not run over the submitted text"). Nada de `additionalContext` -- o hook nao tem o que dizer e gastaria token do usuario a cada prompt. Dono indeterminavel (`session_id` vazio) NAO libera nada: dono vazio casa com claim de dono vazio de qualquer sessao (ASM-009). Entra em `HOOKS_SPECS` com matcher vazio; `install.py` tem verificacao por sha256 e contagem de entrypoints -- os dois sobem de 8 para 9 e `test_install.py` precisa acompanhar.

## T-033 - Gate de custo e instalacao [concluida]

- Refs: AC-024
- Arquivos: tools/bench_hooks.py, .specs/coordenacao-multissessao/medicao-perf.md
- Notas: o hook novo roda no caminho do PROMPT, nao no caminho quente de ferramenta, mas o gate segue p95 < 150 ms. Atencao ao gate de perf existente: `tests/test_perf.py` usa `N_EXECUCOES=20`, e p95 de 20 amostras e quase o segundo maior valor -- um outlier reprova (reprovacoes intermitentes medidas o dia todo em 17/09, inclusive no HEAD limpo). Instalacao e FRONTEIRA DE APROVACAO (Q-008): escreve no `settings.json` global e afeta todas as sessoes abertas -- exige OK do Vinicius, `ListAgents` e `SendMessage` as peers antes.
- INSTALADO em 17/09 16:09 com OK explicito do Vinicius, depois de `--dry-run` e de avisar as peers vivas (`home`, `home-smooth-micali`; a `home-piped-liskov` ja havia encerrado). **Verificado em quatro pontos, nao pela linha de log:** (1) `settings.json` lista `coord_user_prompt.py` no `UserPromptSubmit`, AO LADO do `context_alert.py` de terceiro, que continua la -- 24 hooks no total, 15 de terceiros intactos; (2) sha256 dos **9 de 9** entrypoints instalados batem com o repo; (3) backup datado criado (`settings.json.bak-20260917-160928`); (4) o hook **instalado** (nao o do repo) rodado com `CCOORD_HOME` de teste: exit 0, stdout VAZIO, e o claim de turno foi de presente para ausente. Nota: o conserto da T-035 ja estava no ar antes desta instalacao, porque os hooks importam de `CCOORD_SRC`, que aponta para o repo.

## T-037 - A2 da auditoria: escrita em background do main [concluida]

- Refs: AC-024
- Arquivos: .spec/features/coordenacao-multissessao/tasks.md
- Notas: DESFECHO = divida declarada com numero, nao codigo. O achado A2 dizia que o claim de turno que protege escrita de processo em BACKGROUND do main morre no prompt seguinte enquanto o processo ainda escreve (silencio indevido). **Medido antes de consertar, e o dano nao justifica codigo.** A fonte obvia nao serviu: `purpose` esta **100% vazio** nos 4.896 claims de turno do log -- o campo existe no schema e nunca e preenchido (achado lateral, nao consertado aqui). Medicao valida veio dos transcripts, classificando os comandos com o `classify.classify` REAL (nao heuristica minha): **171 de 12.838 chamadas de Bash usaram `run_in_background` (1,33%), e dessas apenas 13 geram claim de arquivo (7,6%) -- 0,1% do total**. E as 13 nao sao o caso de risco: quase todas redirecionam log de servidor para o scratchpad/temp (`pnpm start > .../log`), mais um `cp` para temp. **Nenhuma escreve em arquivo compartilhado que uma peer fosse editar**, que e a premissa do dano. Conserto exigiria distinguir claim de background no momento da aquisicao (campo novo, atravessando classify->policy->claims) para evitar ~0,1% de casos que nao colidem com ninguem. Reavaliar se o padrao de uso mudar. **Residuo notado de passagem:** dois dos 13 alvos sao `file:-echo` e `file:-pip`, fragmento de comando que o filtro da T-024 deixou passar -- e o ruido ja conhecido (o filtro pega 42% dos fragmentos, nao 100%), nao um defeito novo.

## T-034 - Provar no uso real, nao so no teste [pendente]

- Refs: AC-024
- Arquivos: .specs/coordenacao-multissessao/medicao-fronteira-de-turno.md
- Notas: prova PASS nao prova caminho ponta a ponta (foi o caso do AC-007). Repetir a medicao de fronteira sobre uma janela nova de uso real depois de instalado: a fracao de ciclos que atravessam >=1 prompt tem de cair de 20,1% para ~0 E as disputas de "mesmo turno" (40 em 5 dias, que sao a feature funcionando) NAO podem virar 0 -- se virarem, o release esta comendo turno vivo. Os dois lados, senao a medida nao vale.

## T-035 - O JSON do hook sai em cp1252, nao em UTF-8 [concluida]

- Refs: AC-005
- Arquivos: src/ccoord/hookio.py, tests/test_t035_encoding_stdout.py, tools/probe/deny_encoding.py, tools/probe/probe-settings5.json, tools/probe/run_probe5.sh
- Resultado: MEDIDO em runtime, experimento PAREADO (`run_probe5.sh`: dois `deny` com a MESMA razao acentuada, mudando so o `ensure_ascii`), lido no TRANSCRIPT e nao perguntado ao modelo. `ensure_ascii=False` -> o modelo recebeu `a sess<FFFD>o home est<FFFD> na <FFFD>rea`; `ensure_ascii=True` -> recebeu integro. **A hipotese do pior caso ("o JSON inteiro falha no parse e o deny fica MUDO") foi REFUTADA neste build**: os dois bloquearam, o harness decodifica com substituicao. O dano e o aviso chegar ilegivel a peer e o caminho de arquivo citado nele deixar de bater com o arquivo real -- serio, mas nao e furo de gate. Conserto: `ensure_ascii=True` em `_imprimir` (ponto unico). Mutante provado morto (FAILED failures=1 errors=1) com restauracao conferida por sha256. **Achado colateral que vale mais que o conserto: `_run_hook` de `test_entrypoints.py` seta `PYTHONIOENCODING=utf-8` no subprocesso, e o harness NAO seta** -- a suite inteira vinha medindo uma condicao que nao e a de producao, por isso 338 testes verdes com o defeito vivo. O teste novo roda SEM a variavel e afirma os BYTES, com controle de nao-vacuidade que falha se o ambiente do proprio teste nao expuser o defeito.
- Notas: achado em 17/09 por aviso da peer `home-piped-liskov` (que mediu o texto chegando corrompido ao modelo no `context_alert.py` dela) e CONFIRMADO aqui com medicao propria: `python -c "print(json.dumps(..., ensure_ascii=False))"` com stdout em PIPE -- que e como o hook roda -- devolve `sess\xe3o`, `\xc1rea`, bytes que NAO sao UTF-8 valido (`sys.stdout.encoding` em pipe = **cp1252** nesta maquina, sem `PYTHONIOENCODING` no ambiente do hook). `hookio._imprimir` (hookio.py:246) faz exatamente isso, e TODO aviso desta feature tem acento: "sessao", "colisao", e os caminhos do vault ("Inteligencia de Mercado", "Area de Trabalho"). Conserto e uma linha (`ensure_ascii=True` -> escapes `\uXXXX`, ASCII puro, sempre valido) -- mas MEDIR o sintoma real antes: a peer viu texto corrompido (o harness parseou e trocou o caractere), e o pior caso possivel e o JSON inteiro falhar no parse, que faz o `deny` sumir em SILENCIO -- gate mudo, o pior modo de falha desta feature. A escrita em ARQUIVO (`events.log`, claims) nao tem o problema: la o `ensure_ascii=False` e seguido de `.encode("utf-8")` explicito. Nao mexer sem teste que afirme os BYTES do stdout do entrypoint, nao a string em Python.

## T-014 — Fechamento mecânico [concluida]
- Refs: AC-001, AC-002, AC-003, AC-004, AC-005, AC-006, AC-007, AC-008, AC-009, AC-010, AC-011, AC-012, AC-013, AC-014, AC-015, AC-016, AC-017
- Arquivos: .spec/verification/coordenacao-multissessao.json
- Notas: `onp-spec verify` + `audit --ci`; não declarar pronto sem 0 ERRO.

## T-015 — Registro no vault e na memória [concluida]
- Refs: AC-012
- Arquivos: .specs/coordenacao-multissessao/RETOMAR.md
- Notas: Edit pontual no MEMORY.md, nunca reescrita.
