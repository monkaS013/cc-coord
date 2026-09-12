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

## T-013 — Ensaio ponta a ponta com duas sessões reais [em-andamento]
- Refs: AC-003, AC-012, AC-016
- Arquivos: tools/ensaio/roteiro.md
- Notas: verificação por grep no transcript JSONL — o modelo é testemunha não confiável do que recebeu.

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

## T-014 — Fechamento mecânico [concluida]
- Refs: AC-001, AC-002, AC-003, AC-004, AC-005, AC-006, AC-007, AC-008, AC-009, AC-010, AC-011, AC-012, AC-013, AC-014, AC-015, AC-016, AC-017
- Arquivos: .spec/verification/coordenacao-multissessao.json
- Notas: `onp-spec verify` + `audit --ci`; não declarar pronto sem 0 ERRO.

## T-015 — Registro no vault e na memória [concluida]
- Refs: AC-012
- Arquivos: .specs/coordenacao-multissessao/RETOMAR.md
- Notas: Edit pontual no MEMORY.md, nunca reescrita.
